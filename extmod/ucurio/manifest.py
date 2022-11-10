# This list of package files doesn't include task.py because that's provided
# by the C module.
package(
    "ucurio",
    (
        "__init__.py",
        "errors.py",
        "io.py",
        "kernel.py",
        "meta.py",
        "monitor.py",
        "network.py",
        "queue.py",
        "sched.py",
        "selectors.py",
        "socket.py",
        "ssl.py",
        "sync.py",
        "task.py",
        "time.py",
        "timequeue.py",
        "traps.py",
        "util.py",
        "workers.py",
    ),
    base_path="..",
    opt=3,
)
