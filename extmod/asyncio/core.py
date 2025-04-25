# MicroPython asyncio module
# MIT license; Copyright (c) 2019 Damien P. George

from time import ticks_ms as ticks, ticks_diff, ticks_add
from select import POLLIN, POLLOUT
import sys, select

# Import TaskQueue and Task, preferring built-in C code over Python code
try:
    from _asyncio import TaskQueue, Task
except:
    from .task import TaskQueue, Task

try:
    from _thread import get_ident
except:
    get_ident = lambda: 0


################################################################################
# Exceptions


class CancelledError(BaseException):
    pass


class TimeoutError(Exception):
    pass


# Used when calling Loop.call_exception_handler
_exc_context = {"message": "Task exception wasn't retrieved", "exception": None, "future": None}


################################################################################
# Sleep functions


# "Yield" once, then raise StopIteration
class SingletonGenerator:
    def __init__(self):
        self.state = None
        self.exc = StopIteration()

    def __iter__(self):
        return self

    def __next__(self):
        if self.state is not None:
            loop = get_event_loop()
            loop._task_queue.push(loop.cur_task, self.state)
            self.state = None
            return None
        else:
            self.exc.__traceback__ = None
            raise self.exc


# Pause task execution for the given time (integer in milliseconds, uPy extension)
# Use a SingletonGenerator to do it without allocating on the heap
def sleep_ms(t, sgen=SingletonGenerator()):
    assert sgen.state is None
    sgen.state = ticks_add(ticks(), max(0, t))
    return sgen


# Pause task execution for the given time (in seconds)
def sleep(t):
    return sleep_ms(int(t * 1000))


################################################################################
# Queue and poller for stream IO


class IOQueue:
    def __init__(self, loop: 'Loop'):
        self.poller = select.poll()
        self.loop = loop
        self.map = {}  # maps id(stream) to [task_waiting_read, task_waiting_write, stream]

    def _enqueue(self, s, idx):
        cur_task = self.loop.cur_task
        id_s = id(s)
        if id_s not in self.map:
            entry = [None, None, s]
            entry[idx] = cur_task
            self.map[id_s] = entry
            self.poller.register(s, POLLIN if idx == 0 else POLLOUT)
        else:
            sm = self.map[id_s]
            assert sm[idx] is None
            assert sm[1 - idx] is not None
            sm[idx] = cur_task
            self.poller.modify(s, POLLIN | POLLOUT)
        # Link task to this IOQueue so it can be removed if needed
        cur_task.data = self

    def _dequeue(self, s):
        del self.map[id(s)]
        self.poller.unregister(s)

    def queue_read(self, s):
        self._enqueue(s, 0)

    def queue_write(self, s):
        self._enqueue(s, 1)

    def remove(self, task):
        while True:
            del_s = None
            for k in self.map:  # Iterate without allocating on the heap
                q0, q1, s = self.map[k]
                if q0 is task or q1 is task:
                    del_s = s
                    break
            if del_s is not None:
                self._dequeue(s)
            else:
                break

    def wait_io_event(self, dt):
        _task_queue = self.loop._task_queue
        for s, ev in self.poller.ipoll(dt):
            sm = self.map[id(s)]
            # print('poll', s, sm, ev)
            if ev & ~POLLOUT and sm[0] is not None:
                # POLLIN or error
                _task_queue.push(sm[0])
                sm[0] = None
            if ev & ~POLLIN and sm[1] is not None:
                # POLLOUT or error
                _task_queue.push(sm[1])
                sm[1] = None
            if sm[0] is None and sm[1] is None:
                self._dequeue(s)
            elif sm[0] is None:
                self.poller.modify(s, POLLOUT)
            else:
                self.poller.modify(s, POLLIN)


################################################################################
# Main run loop


# Ensure the awaitable is a task
def _promote_to_task(aw):
    return aw if isinstance(aw, Task) else get_event_loop().create_task(aw)


# Create and schedule a new task from a coroutine
def create_task(coro):
    return get_event_loop().create_task(coro)


# Keep scheduling tasks until there are none left to schedule
def _run_until_complete(loop: 'Loop', main_task=None):
    _task_queue = loop._task_queue
    _io_queue = loop._io_queue
    excs_all = (CancelledError, Exception)  # To prevent heap allocation in loop
    excs_stop = (CancelledError, StopIteration)  # To prevent heap allocation in loop
    while True:
        # Wait until the head of _task_queue is ready to run
        dt = 1
        while dt > 0:
            dt = -1
            t = _task_queue.peek()
            if t:
                # A task waiting on _task_queue; "ph_key" is time to schedule task at
                dt = max(0, ticks_diff(t.ph_key, ticks()))
            elif not _io_queue.map:
                # No tasks can be woken so finished running
                loop.cur_task = None
                return
            # print('(poll {})'.format(dt), len(_io_queue.map))
            _io_queue.wait_io_event(dt)

        # Get next task to run and continue it
        t = _task_queue.pop()
        loop.cur_task = t
        try:
            # Continue running the coroutine, it's responsible for rescheduling itself
            exc = t.data
            if not exc:
                t.coro.send(None)
            else:
                # If the task is finished and on the run queue and gets here, then it
                # had an exception and was not await'ed on.  Throwing into it now will
                # raise StopIteration and the code below will catch this and run the
                # call_exception_handler function.
                t.data = None
                t.coro.throw(exc)
        except excs_all as er:
            # Check the task is not on any event queue
            assert t.data is None
            # This task is done, check if it's the main task and then loop should stop
            if t is main_task:
                loop.cur_task = None
                if isinstance(er, StopIteration):
                    return er.value
                raise er
            if t.state:
                # Task was running but is now finished.
                waiting = False
                if t.state is True:
                    # "None" indicates that the task is complete and not await'ed on (yet).
                    t.state = None
                elif callable(t.state):
                    # The task has a callback registered to be called on completion.
                    t.state(t, er)
                    t.state = False
                    waiting = True
                else:
                    # Schedule any other tasks waiting on the completion of this task.
                    while t.state.peek():
                        _task_queue.push(t.state.pop())
                        waiting = True
                    # "False" indicates that the task is complete and has been await'ed on.
                    t.state = False
                if not waiting and not isinstance(er, excs_stop):
                    # An exception ended this detached task, so queue it for later
                    # execution to handle the uncaught exception if no other task retrieves
                    # the exception in the meantime (this is handled by Task.throw).
                    _task_queue.push(t)
                # Save return value of coro to pass up to caller.
                t.data = er
            elif t.state is None:
                # Task is already finished and nothing await'ed on the task,
                # so call the exception handler.

                # Save exception raised by the coro for later use.
                t.data = exc

                # Create exception context and call the exception handler.
                _exc_context["exception"] = exc
                _exc_context["future"] = t
                loop.call_exception_handler(_exc_context)


# Create a new task from a coroutine and run it until it finishes
def run(coro):
    return get_event_loop().run_until_complete(create_task(coro))


def run_until_complete(main_task=None):
    return get_event_loop().run_until_complete(main_task)

################################################################################
# Event loop wrapper


async def _stopper():
    pass


# Provide transparent access to loop-local variables to appear as module 
# properties.
def __getattr__(name):
    # cur_task, _task_queue, _io_queue
    return getattr(get_event_loop(), name)



class Loop:
    _exc_handler = None
    _stop_task = None
    cur_task = None

    def __init__(self):
        self._task_queue = TaskQueue()
        self._io_queue = IOQueue(self)

    def create_task(self, coro):
        if not hasattr(coro, "send"):
            raise TypeError("coroutine expected")
        t = Task(coro, self, globals())
        self._task_queue.push(t)
        return t

    def run_forever(self):
        self._stop_task = st = Task(_stopper(), self, globals())
        _run_until_complete(self, st)
        # TODO should keep running until .stop() is called, even if there're no tasks left

    def run_until_complete(self, aw):
        return _run_until_complete(self, _promote_to_task(aw))

    def stop(self):
        if self._stop_task is not None:
            self._task_queue.push(self._stop_task)
            # If stop() is called again, do nothing
            self._stop_task = None

    def close(self):
        pass

    def set_exception_handler(self, handler):
        self._exc_handler = handler

    def get_exception_handler(self):
        return self._exc_handler

    def default_exception_handler(loop, context):
        print(context["message"], file=sys.stderr)
        print("future:", context["future"], "coro=", context["future"].coro, file=sys.stderr)
        sys.print_exception(context["exception"], sys.stderr)

    def call_exception_handler(self, context):
        (self._exc_handler or Loop.default_exception_handler)(self, context)


# The runq_len and waitq_len arguments are for legacy uasyncio compatibility
_loops = {}
def get_event_loop(runq_len=0, waitq_len=0):
    ident = get_ident()
    if ident in _loops:
        return _loops[ident]

    return new_event_loop()


def current_task():
    cur_task = get_event_loop().cur_task
    if cur_task is None:
        raise RuntimeError("no running event loop")
    return cur_task


def new_event_loop():
    loop = Loop()
    _loops[get_ident()] = loop
    return loop


# Initialise default event loop
new_event_loop()
