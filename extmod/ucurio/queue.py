# curio/queue.py
#
# A few different queue structures.

# -- Standard library

from collections import deque
from heapq import heappush, heappop

# -- Curio

from .sched import SchedFIFO, SchedBarrier

__all__ = ['Queue', 'PriorityQueue', 'LifoQueue']

class QueueBase:
    '''
    Base class for queues used to communicate between Curio tasks.
    Not safe for use with external threads or processes.
    '''

    def __init__(self, maxsize=0):
        self.maxsize = maxsize
        self._get_waiting = SchedFIFO()
        self._put_waiting = SchedFIFO()
        self._join_waiting = SchedBarrier()
        self._task_count = 0
        self._queue = self._init_internal_queue()

    def __repr__(self):
        res = super().__repr__()
        return '<%s, len=%d>' % (res[1:-1], len(self._queue))

    def empty(self):
        return not self._queue

    def full(self):
        return self.maxsize and len(self._queue) == self.maxsize

    async def get(self):
        must_wait = bool(self._get_waiting)
        while must_wait or self.empty():
            must_wait = False
            await self._get_waiting.suspend('QUEUE_GET')

        result = self._get_item()

        if self._put_waiting:
            await self._put_waiting.wake()
        return result

    async def join(self):
        if self._task_count > 0:
            await self._join_waiting.suspend('QUEUE_JOIN')

    async def put(self, item):
        while self.full():
            await self._put_waiting.suspend('QUEUE_PUT')
        self._put_item(item)
        self._task_count += 1
        if self._get_waiting:
            await self._get_waiting.wake()

    def qsize(self):
        return len(self._queue)

    async def task_done(self):
        self._task_count -= 1
        if self._task_count == 0 and self._join_waiting:
            await self._join_waiting.wake()


# The following classes implement the low-level queue data structure
# and policies for adding and removing items.

class FIFOImpl:

    def _init_internal_queue(self):
        return deque(tuple(), 0)

    def _get_item(self):
        return self._queue.popleft()

    def _unget_item(self, item):
        self._queue.appendleft(item)

    def _put_item(self, item):
        self._queue.append(item)


class PriorityImpl:

    def _init_internal_queue(self):
        return []

    def _get_item(self):
        return heappop(self._queue)

    def _put_item(self, item):
        heappush(self._queue, item)

    _unget_item = _put_item


class LIFOImpl:

    def _init_internal_queue(self):
        return []

    def _put_item(self, item):
        self._queue.append(item)

    def _get_item(self):
        return self._queue.pop()

    _unget_item = _put_item

# Concrete Queue implementations

class Queue(QueueBase, FIFOImpl):
    '''
    A First-In First-Out queue for communicating between Curio tasks.
    not safe for communicating between Curio and external
    threads, processes, etc.
    '''
    pass

class PriorityQueue(QueueBase, PriorityImpl):
    '''
    A priority queue for communicating between Curio tasks.
    not safe for communicating between Curio and external
    threads, processes, etc.
    '''
    pass


class LifoQueue(QueueBase, LIFOImpl):
    '''
    A Last-In First-Out queue for communicating between Curio tasks.
    Not safe for communicating between Curio and external
    threads, processes, etc.
    '''
    pass
