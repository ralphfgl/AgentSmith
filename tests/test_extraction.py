import pytest
from agentsmith.extraction import extract_executable_code, ExtractionResult


@pytest.mark.parametrize(
    "input_text, expected_code, expected_warning",
    [
        # 1. Standard Fenced Markdown Blocks (Python and Py)
        (
            "Here is the solution:\n```python\nprint('hello')\n```\nHope it helps!",
            "print('hello')",
            None,
        ),
        (
            "```py\nx = 10\ny = 20\n```",
            "x = 10\ny = 20",
            None,
        ),
        # 2. Case Insensitivity & Empty Content inside Fences
        (
            "```PYTHON\nimport sys\n```",
            "import sys",
            None,
        ),
        (
            "```python\n```",
            "",
            None,
        ),
        # 3. Unclosed / Malformed Opening Fences
        (
            "Some conversation here.\n```python\na = 5\nb = 6",
            "a = 5\nb = 6",
            "Malformed code block: opening fence had no closing fence; interpreted trailing text as Python.",
        ),
        # 4. JSON / Hermes Tool Call Formats
        (
            '<tool_call>\n{"name": "calculate_tax", "arguments": {"income": 50000}}\n</tool_call>',
            "result = calculate_tax(income=50000)\nprint(result)",
            "Converted JSON/Hermes tool call to Python.",
        ),
        (
            '{"tool_name": "get_weather", "args": {"city": "Paris"}}',
            "result = get_weather(city='Paris')\nprint(result)",
            "Converted JSON/Hermes tool call to Python.",
        ),
        # 5. XML Tool Invoke Formats
        (
            '<invoke name="fetch_user"><parameter name="user_id">123</parameter></invoke>',
            "result = fetch_user(user_id=123)\nprint(result)",
            "Converted XML invoke tool call to Python.",
        ),
        (
            '<invoke name="search"><parameter name="query">\'machine learning\'</parameter></invoke>',
            "result = search(query='machine learning')\nprint(result)",
            "Converted XML invoke tool call to Python.",
        ),
        # 6. ReAct Framework Action / Action Inputs
        (
            "Thought: I need to call a tool.\nAction: calculate_square\nAction Input: 16",
            "result = calculate_square(input=16)\nprint(result)",
            "Converted ReAct Action / Action Input to Python.",
        ),
        (
            'Action: run_query\nAction Input: {"db": "prod", "id": 42}',
            "result = run_query(db='prod', id=42)\nprint(result)",
            "Converted ReAct Action / Action Input to Python.",
        ),
        # 7. Raw Valid Python (No Fences Fallback)
        (
            "import math\ndef double(x):\n    return x * 2",
            "import math\ndef double(x):\n    return x * 2",
            "No code fence found; interpreted the whole response as Python.",
        ),
        # 8. Conversational Human Conversational Chat (Should fail ast.parse validation)
        (
            "Hello! I am an AI assistant. I cannot write code for this request.",
            "",
            "No valid Python code block or supported tool-call format was found.",
        ),
        # 9. Empty or purely white space inputs
        (
            "   \n  \n ",
            "",
            "No valid Python code block or supported tool-call format was found.",
        ),
    ],
)
def test_extract_executable_code_variants(
    input_text, expected_code, expected_warning
):
    """Verifies that all variations of LLM string patterns yield targeted code and warnings."""
    result = extract_executable_code(input_text)
    assert isinstance(result, ExtractionResult)
    assert result.code == expected_code
    assert result.warning == expected_warning


def test_raw_text_max_length_boundary():
    """Verifies that raw text longer than 4,000 characters is rejected by the fallback layer."""
    # Create valid python syntax string that surpasses the 4,000 token limit threshold
    huge_valid_python = "x = 1\n" * 800  # 4200 characters long

    result = extract_executable_code(huge_valid_python)
    assert result.code == ""
    assert "No valid Python code block" in result.warning


def test_malformed_xml_graceful_degradation():
    """Ensures bad XML notation safely routes past the XML extractor down to the next parser layer."""
    bad_xml = (
        '<invoke name="broken"><parameter>Missing name attrib</parameter>'
    )

    # It shouldn't crash with ElementTree.ParseError; it should fallback to no code found
    result = extract_executable_code(bad_xml)
    assert result.code == ""
