from collections import namedtuple
import math
import select

# ... Adapted from the Python 3.11 source code

# generic events, that must be mapped to implementation-specific ones
EVENT_READ = (1 << 0)
EVENT_WRITE = (1 << 1)

SelectorKey = namedtuple('SelectorKey', ['fileobj', 'id', 'events', 'data'])


class BaseSelector:
    def __init__(self):
        self._map = dict()

    def register(self, fileobj, events, data=None):
        if (not events) or (events & ~(EVENT_READ | EVENT_WRITE)):
            raise ValueError("Invalid events: {!r}".format(events))

        key = SelectorKey(fileobj, id(fileobj), events, data)

        if id(fileobj) in self._map:
            raise KeyError("{!r} is already registered"
                           .format(fileobj))

        self._map[id(fileobj)] = key
        return key

    def unregister(self, fileobj):
        try:
            key = self._map.pop(id(fileobj))
        except KeyError:
            raise KeyError("{!r} is not registered".format(fileobj))
        return key

    def modify(self, fileobj, events, data=None):
        try:
            key = self._map[fileobj]
        except KeyError:
            raise KeyError("{!r} is not registered".format(fileobj))
        if events != key.events:
            self.unregister(fileobj)
            key = self.register(fileobj, events, data)
        elif data != key.data:
            # Use a shortcut to update the data.
            key = key._replace(data=data)
            self._map[id(fileobj)] = key
        return key

    def close(self):
        self._map = None

    def get_key(self, fileobj):
        """Return the key associated to a registered file object.
        Returns:
        SelectorKey for this file object
        """
        mapping = self._map
        if mapping is None:
            raise RuntimeError('Selector is closed')
        try:
            return mapping[id(fileobj)]
        except KeyError:
            raise KeyError("{!r} is not registered".format(fileobj))

    def get_map(self):
        return self._map

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class PollSelector(BaseSelector):
    """Base class shared between poll, epoll and devpoll selectors."""
    _selector_cls = select.poll
    _EVENT_READ = select.POLLIN
    _EVENT_WRITE = select.POLLOUT

    def __init__(self):
        super().__init__()
        self._selector = self._selector_cls()

    def register(self, fileobj, events, data=None):
        key = super().register(fileobj, events, data)
        poller_events = 0
        if events & EVENT_READ:
            poller_events |= self._EVENT_READ
        if events & EVENT_WRITE:
            poller_events |= self._EVENT_WRITE
        try:
            self._selector.register(key.fileobj, poller_events)
        except:
            super().unregister(fileobj)
            raise
        return key

    def unregister(self, fileobj):
        key = super().unregister(fileobj)
        try:
            self._selector.unregister(key.fileobj)
        except OSError:
            # This can happen if the FD was closed since it
            # was registered.
            pass
        return key

    def modify(self, fileobj, events, data=None):
        try:
            key = self._map[fileobj]
        except KeyError:
            raise KeyError(f"{repr(fileobj)} is not registered")

        changed = False
        if events != key.events:
            selector_events = 0
            if events & EVENT_READ:
                selector_events |= self._EVENT_READ
            if events & EVENT_WRITE:
                selector_events |= self._EVENT_WRITE
            try:
                self._selector.modify(key.fd, selector_events)
            except:
                super().unregister(fileobj)
                raise
            changed = True
        if data != key.data:
            changed = True

        if changed:
            key = key._replace(events=events, data=data)
            self._map[fileobj] = key
        return key

    def select(self, timeout=None):
        # This is shared between poll() and epoll().
        # epoll() has a different signature and handling of timeout parameter.
        if timeout is None:
            timeout = None
        elif timeout <= 0:
            timeout = 0
        ready = []
        try:
            fd_event_list = self._selector.ipoll(timeout)
        except OSError:
            return ready
        for fd, event in fd_event_list:
            events = 0
            if event & ~self._EVENT_READ:
                events |= EVENT_WRITE
            if event & ~self._EVENT_WRITE:
                events |= EVENT_READ

            key = self._map[id(fd)]
            if key:
                ready.append((key, events & key.events))
        return ready