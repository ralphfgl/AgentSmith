# basic example eval
# code_string = "x * y + 10"
# compiled_code = compile(code_string, filename="<math_formula>", mode="eval")
# context = {"x": 5, "y": 2}  # execute inside a context namespace dictionary
# result = eval(compiled_code, context)
# print(result)
#
# # basic example exec
# script_string = """
# total = 0
# for i in range(1, 6):
# 	total += i
# print(f"the total run sum is: {total}")
# """
# compiled_script = compile(script_string, filename="<loop_script>", mode="exec")
# exec(compiled_script)


## inspecting the current running context
# import sys
#
#
# def outer_function():
#     x = "hello from outer"
#     inner_function()
#
#
# def inner_function():
#     y = "hello from inner"
#
#     current_frame = sys._getframe(0)
#     print(f"Current Function: {current_frame.f_code.co_name}")
#     print(f"Current Locals: {current_frame.f_locals}")
#
#     caller_frame = current_frame.f_back
#     print(f"Caller Function: {caller_frame.f_code.co_name}")
#     print(f"Caller Locals: {caller_frame.f_locals}")
#
#
# outer_function()

# import asyncio
#
#
# # define an asynchronous coroutine
# async def say_hello():
#     # simulate waiting for I/O without blocking the cpu
#     await asyncio.sleep(3)
#     print("Hello from asyncio")
#
#
# async def main():
#     await say_hello()
#
#
# if __name__ == "__main__":
#     # start the event loop and run the main entry point
#     asyncio.run(main())


# import threading
# import time
#
#
# def say_hello():
#     time.sleep(3)
#     print("hello from threading")
#
#
# if __name__ == "__main__":
#     my_thread = threading.Thread(target=say_hello)
#     my_thread.start()
#     my_thread.join()

# import multiprocessing
# import time
#
#
# def say_hello():
#     time.sleep(3)
#     print("hello from multiprocessing")
#
#
# # !!! always use the __name__ guard for multprocessing
# if __name__ == "__main__":
#     # create a separate process pointing at our target function
#     my_process = multiprocessing.Process(target=say_hello)
#     my_process.start()
#     my_process.join()

# Capture output into a string var

# import io
# from contextlib import redirect_stdout, redirect_stderr
# from sys import stderr
#
#
# def noisy_function():
#     print("blablablablabbla")
#     import sys
#
#     print("errrorr", file=sys.stderr)
#
#
# # in memory string streams
# stdout_buffer = io.StringIO()
# stderr_buffer = io.StringIO()
#
# # capture stdout and stderr
# with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
#     noisy_function()
#
# # retrive the text as std python string
# captured_stdout = stdout_buffer.getvalue()
# captured_stderr = stderr_buffer.getvalue()
#
# print(f"capture out: {captured_stdout.strip()}")
# print(f"capture err: {captured_stderr.strip()}")

# create mutable buffer of bytes
# data = bytearray(b"abcdefghijklmnopqrstuvwxyz")
# # wrap it in a memory view
# view = memoryview(data)
# # slice the view (this do not duplicate the memory in RAM)
# middle_slice = view[10:15]
# # because its a view, mutating the slice mutates the original
# middle_slice[0] = ord("X")
# print(data)

# # create custom context manager
# from contextlib import contextmanager
# import time
#
#
# @contextmanager
# def timer_context():
#     # 1. setup when entering the context
#     start_time = time.time()
#     print("timer started")
#     try:
#         # 2. hand over control to the 'with' block
#         yield
#     finally:
#         # 3. teardown
#         end_time = time.time()
#         print(f"timer stoped {end_time - start_time}")
#
#
# with timer_context():
#     print("doing some work")
#     time.sleep(1.5)
#
#
# # using a class, implementing __enter__ and __exit__
# class DatabaseConnection:
#     def __init__(self, db_name):
#         self.db_name = db_name
#
#     def __enter__(self):
#         # 1. setup: open connection and return ressource
#         print(f" Connection to database {self.db_name}")
#         return self  # this is what goes in the 'as' var
#
#     def __exit__(self, exc_type, exc_val, exc_tb):
#         # 2. teardown: close the conn
#         print(f"closing the conneciton")
#         if exc_type:
#             print(f"an error occured")
#         return False  # to let python raise the error normally or true to suppress it
#
#
# with DatabaseConnection("production_db") as db:
#     print("fetching data")

# learn urllib
# from urllib.request import urlopen
#
# url = "https://example.com"
# with urlopen(url) as response:
#     # response body initially returns as raw bytes
#     raw_html = response.read()
#     # decode the bytes into a readable string
#     html_string = raw_html.decode("utf-8")
#     print(f"Status Code: {response.status}")
#     print(raw_html[:200])
#     print()
#     print(html_string[:200])

# from urllib.parse import urlencode
# from urllib.request import urlopen
#
# base_url = "https://httpbin.org"
# params = {"search": "python tutorial", "limit": 5}
#
# query_string = urlencode(params)
# full_url = f"{base_url}?{query_string}"
# print(query_string)
# with urlopen(full_url) as response:
#     print(response.read().decode("utf-8"))

# import datetime
# today = datetime.date(2026, 9, 29)
# print(repr(today))
# print(str(today))

import asyncio
import time


async def brew_coffee():
    print("start brewing coffee")
    await asyncio.sleep(2)
    print("coffee ready")
    return "Capucino"


async def toast_bread():
    print("putting bread in toaster")
    await asyncio.sleep(1)
    print("toast ready")
    return "toas"


async def main():
    start_time = time.perf_counter()
    # grabs a reference to an running event loop
    loop = asyncio.get_running_loop()
    print(class_name := f"using loop {loop.__class__.__name__}")
    print("start breakfast")
    # launch async task concurently
    breakfast = await asyncio.gather(brew_coffee(), toast_bread())
    end_time = time.perf_counter()
    print(f"served: {breakfast}")
    print(f"time : {end_time - start_time}")


# run create the event loop
asyncio.run(main())
