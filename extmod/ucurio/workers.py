# curio/workers.py
#
# Functions for performing work outside of curio.  This includes
# running functions in threads, processes, and executors from the
# concurrent.futures module.

__all__ = ['run_in_executor', 'run_in_thread', 'Future']

# -- Standard Library

try:
    import traceback
except:
    pass

# -- Curio

from .errors import CancelledError
from .traps import _future_wait, _get_kernel
from .util import Thread
from . import sync


# Code to embed a traceback in a remote exception.  This is borrowed
# straight from multiprocessing.pool.  Copied here to avoid possible
# confusion when reading the traceback message (it will identify itself
# as originating from curio as opposed to multiprocessing.pool).

class RemoteTraceback(Exception):

    def __init__(self, tb):
        self.tb = tb

    def __str__(self):
        return self.tb


class ExceptionWithTraceback:

    def __init__(self, exc, tb):
        tb = traceback.format_exception(type(exc), exc, tb)
        tb = ''.join(tb)
        self.exc = exc
        self.tb = '\n"""\n%s"""' % tb

    def __reduce__(self):
        return rebuild_exc, (self.exc, self.tb)


def rebuild_exc(exc, tb):
    exc.__cause__ = RemoteTraceback(tb)
    return exc

async def run_in_executor(exc, callable, *args):
    '''
    Run callable(*args) in an executor such as
    ThreadPoolExecutor or ProcessPoolExecutor from the
    concurrent.futures module.  Be aware that on cancellation, any
    worker thread or process that was handling the request will
    continue to run to completion as a kind of zombie-- possibly
    rendering the executor unusable for subsequent work.

    This function is provided for compatibility with
    concurrent.futures, but is not the recommend approach for running
    blocking or cpu-bound work in curio. Use the run_in_thread() or
    run_in_process() methods instead.
    '''
    future = exc.submit(callable, *args)
    await _future_wait(future)
    return future.result()

MAX_WORKER_THREADS = 1

async def reserve_thread_worker():
    '''
    Reserve a thread pool worker
    '''
    kernel = await _get_kernel()
    if not hasattr(kernel, 'thread_pool'):
        kernel.thread_pool = WorkerPool(ThreadWorker, MAX_WORKER_THREADS)
        kernel._call_at_shutdown(kernel.thread_pool.shutdown)
    return (await kernel.thread_pool.reserve())

async def run_in_thread(callable, *args, call_on_cancel=None):
    '''
    Run callable(*args) in a separate thread and return the result. If
    cancelled, be aware that the requested callable may or may not have
    executed.  If it start running, it will run fully to completion
    as a kind of zombie.
    '''
    worker = None
    try:
        worker = await reserve_thread_worker()
        return await worker.apply(callable, args, call_on_cancel)
    finally:
        if worker:
            await worker.release()

# The _FutureLess class is a custom "Future" implementation solely for
# use by curio. It is used by the ThreadWorker class below and
# provides only the minimal set of functionality needed to transmit a
# result back to the curio kernel.  Unlike the normal Future class,
# this version doesn't require any thread synchronization or
# notification support.  By eliminating that, the overhead associated
# with the handoff between curio tasks and threads is substantially
# faster.


class _FutureLess(object):
    __slots__ = ('_callback', '_exception', '_result')

    def set_result(self, result):
        self._result = result
        self._callback(self)

    def set_exception(self, exc):
        self._exception = exc
        self._callback(self)

    def result(self):
        try:
            return self._result
        except AttributeError:
            raise self._exception from None

    def add_done_callback(self, func):
        self._callback = func

    def cancel(self):
        pass

class Future(_FutureLess):
    async def __anext__(self):
        return await _future_wait(self)

# A ThreadWorker represents a thread that performs work on behalf of a
# curio task.   A curio task initiates work by executing the
# apply() method. This passes the request to a background thread that
# executes it.  While this takes place, the curio task blocks, waiting
# for a result to be set on an internal Future.


class ThreadWorker(object):
    '''
    Worker that executes a callable on behalf of a curio task in a separate thread.
    '''

    def __init__(self, pool):
        self.thread = None
        self.start_evt = None
        self.lock = None
        self.request = None
        self.terminated = False
        self.pool = pool

    def _launch(self):
        self.start_evt = threading.Event()
        self.thread = Thread(target=self.run_worker)
        self.thread.start()

    def run_worker(self):
        while True:
            self.start_evt.wait()
            self.start_evt.clear()
            # If there is no pending request, but we were signalled to
            # start, it means terminate.
            if not self.request:
                return

            # Run the request
            self.request()

    async def release(self):
        if self.pool:
            await self.pool.release(self)

    def shutdown(self):
        self.terminated = True
        self.request = None
        if self.start_evt:
            self.start_evt.set()

    async def apply(self, func, args=(), call_on_cancel=None):
        '''
        Run the callable func in a separate thread and return the result.
        '''
        if self.thread is None:
            self._launch()

        # Set up a request for the worker thread
        done_evt = threading.Event()
        done_evt.clear()
        cancelled = False
        future = _FutureLess()

        def run_callable():
            try:
                future.set_result(func(*args))
            except BaseException as err:
                future.set_exception(err)
            finally:
                done_evt.wait()
                if cancelled and call_on_cancel:
                    call_on_cancel(future)

        self.request = run_callable
        try:
            await _future_wait(future, self.start_evt)
            return future.result()
        except CancelledError as e:
            cancelled = True
            self.shutdown()
            raise
        finally:
            done_evt.set()

# Pool of workers for carrying out jobs on behalf of curio tasks.
#
# This pool works a bit differently than a normal thread/process
# pool due to some of the different ways that threads get used in Curio.
# Instead of submitting work to the pool, you use the reserve() method
# to obtain a worker:
#
#     worker = await pool.reserve()
#
# Once you have a worker, it is yours for as long as you want to have
# it.  To submit work to it, use the apply() method:
#
#     await worker.apply(callable, args)
#
# When you're done with it, release it back to the pool.
#
#     await worker.release()
#
# Some rationale for this design:  Sometimes when you're working with
# threads, you want to perform multiple steps and you need to make sure
# you're performing each step on the same thread for some reason. This
# is especially true if you're trying to manage work cancellation.
# For example, work started in a thread might need to be cleaned up
# on the same thread.  By reserving/releasing workers, we get more
# control over the whole process of how workers get managed.

class WorkerPool(object):

    def __init__(self, workercls, nworkers):
        self.nworkers = sync.Semaphore(nworkers)
        self.workercls = workercls
        self.workers = []

    def shutdown(self):
        for worker in self.workers:
            worker.shutdown()
        self.workers = []

    async def reserve(self):
        await self.nworkers.acquire()
        if not self.workers:
            return self.workercls(self)
        else:
            return self.workers.pop()

    async def release(self, worker):
        if not worker.terminated:
            self.workers.append(worker)
        await self.nworkers.release()


# Pool definitions should anyone want to use them directly
ThreadPool = lambda nworkers: WorkerPool(ThreadWorker, nworkers)