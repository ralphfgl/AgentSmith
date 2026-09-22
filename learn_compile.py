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
import sys


def outer_function():
    x = "hello from outer"
    inner_function()


def inner_function():
    y = "hello from inner"

    current_frame = sys._getframe(0)
    print(f"Current Function: {current_frame.f_code.co_name}")
    print(f"Current Locals: {current_frame.f_locals}")

    caller_frame = current_frame.f_back
    print(f"Caller Function: {caller_frame.f_code.co_name}")
    print(f"Caller Locals: {caller_frame.f_locals}")


outer_function()
