# curio/meta.py
#     ___
#     \./      DANGER:  This module implements some experimental
#  .--.O.--.            metaprogramming techniques involving async/await.
#   \/   \/             If you use it, you might die. No seriously.
#

__all__ = [
    'iscoroutinefunction', 'finalize',
    'curio_running', 'instantiate_coroutine',
    '_locals'
 ]

# -- Standard Library
import sys

# -- Curio
from .util import contextmanager


class tlocals: pass
_locals = tlocals()

# Context manager that is used when the kernel is executing.
@contextmanager
def running(kernel):
    if getattr(_locals, 'running', False):
        raise RuntimeError('Only one Curio kernel per thread is allowed')
    _locals.running = True
    _locals.kernel = kernel
    try:
        yield
    finally:
        _locals.running = False
        _locals.kernel = None

def curio_running():
    '''
    Return a flag that indicates whether or not Curio is running in the current thread.
    '''
    return getattr(_locals, 'running', False)

def iscoroutinefunction(func):
    '''
    Modified test for a coroutine function with awareness of functools.partial
    '''
    import inspect
    _isasyncgenfunction = inspect.isasyncgenfunction
    if hasattr(func, '__func__'):
        return iscoroutinefunction(func.__func__)
    return inspect.iscoroutinefunction(func) or hasattr(func, '_awaitable') or _isasyncgenfunction(func)

def instantiate_coroutine(corofunc, *args, **kwargs):
    '''
    Try to instantiate a coroutine. If corofunc is already a coroutine,
    we're done.  If it's a coroutine function, we call it inside an
    async context with the given arguments to create a coroutine.  If
    it's not a coroutine, we call corofunc(*args, **kwargs) and hope
    for the best.
    '''
    if hasattr(corofunc, '__next__'):
        assert not args and not kwargs, "arguments can't be passed to an already instantiated coroutine"
        return corofunc

    async def context():
        return corofunc(*args, **kwargs)

    try:
        context().send(None)
    except StopIteration as e:
        return e.value