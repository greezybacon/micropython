import _thread

class _contextmanager:
    def __init__(self, func, args, kwargs):
        self.gen = func(*args, **kwargs)
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def __enter__(self):
        del self.args, self.kwargs, self.func
        return next(self.gen)

    def __exit__(self, typ, value, traceback):
        if typ is None:
            try:
                next(self.gen)
            except StopIteration:
                return False
        else:
            try:
                self.gen.throw(typ, value, traceback)
            except StopIteration as exc:
                # Suppress StopIteration *unless* it's the same exception that
                # was passed to throw().  This prevents a StopIteration
                # raised inside the "with" statement from being suppressed.
                return exc is not value
            except:
                return False

        raise RuntimeError("Generator did not stop")

    def __call__(self, func):
        def inner(*args, **kwargs):
            with self._new_cm():
                return func(*args, **kwargs)
        return inner

    def _new_cm(self):
        return self.func(*self.args, **self.kwargs)
    
def contextmanager(func):
    def wrapped(*args, **kwargs):
        return _contextmanager(func, args, kwargs)
    return wrapped


class Counter:
    def __init__(self, start=1):
        self.current = start - 1
    
    def __iter__(self):
        return self

    def __next__(self):
        self.current += 1
        return self.current

def partial(func, *pargs, **pkwargs):
    def wrapped(*args, **kwargs):
        return func(*pargs, *args, **pkwargs, **kwargs)
    return wrapped

def wraps(ignored):
    def decorator(func):
        return func
    return decorator

class defaultdict(dict):
    def __init__(self, default_factory=None, *args):
        super().__init__(*args)
        self.default_factory = default_factory

    def __getitem__(self, name):
        if name not in self:
            return self.__missing__(name)

        return super().__getitem__(name)

    def __missing__(self, name):
        if self.default_factory is None:
            raise KeyError(name)
        return self.default_factory()

class Thread:
    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None):
        self.target = target
        self.args = args
        self.kwargs = {} if kwargs is None else kwargs

    def start(self):
        _thread.start_new_thread(self.run, ())

    def run(self):
        self.target(*self.args, **self.kwargs)