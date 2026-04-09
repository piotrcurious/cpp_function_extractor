import subprocess
import os
import shutil
from pathlib import Path

def run_test(name, input_file, targets, expected_strings=None):
    print(f"Running test {name}...")
    output_dir = Path(f"test_run_{name}")
    if output_dir.exists():
        shutil.rmtree(output_dir)

    cmd = ["python3", "refactor_tool.py", input_file, "-o", str(output_dir), "-t"] + targets
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAILED: {name} (refactor_tool crashed)")
        print(result.stderr)
        return False

    cpp_file = output_dir / "extracted_code.cpp"
    header_file = output_dir / "extracted_code.h"
    if not cpp_file.exists():
        print(f"FAILED: {name} (extracted_code.cpp not found)")
        return False

    if expected_strings:
        with open(cpp_file, 'r') as f:
            content = f.read()
        with open(header_file, 'r') as f:
            header_content = f.read()

        full_content = content + header_content
        for s in expected_strings:
            if s not in full_content:
                print(f"FAILED: {name} (Expected string '{s}' not found)")
                return False

    compile_cmd = ["g++", "-c", str(cpp_file), "-o", str(output_dir / "test.o")]
    compile_result = subprocess.run(compile_cmd, capture_output=True, text=True)
    if compile_result.returncode != 0:
        print(f"FAILED: {name} (Compilation failed)")
        print(compile_result.stderr)
        return False

    print(f"PASSED: {name}")
    return True

if __name__ == "__main__":
    tests = [
        ("simple", "examples/example_simple.cpp", ["add", "Point"], ["int add", "struct Point"]),
        ("comprehensive", "examples/comprehensive_test.cpp", ["Status", "Value", "Buffer", "Controller", "absolute", "APP_VERSION"], ["enum class Status", "union Value", "template <typename T, int Size>", "class Controller", "T absolute", "#define APP_VERSION"]),
    ]

    success_count = 0
    for name, input_file, targets, expected in tests:
        if run_test(name, input_file, targets, expected):
            success_count += 1

    print(f"\nSummary: {success_count}/{len(tests)} tests passed.")
    if success_count < len(tests):
        exit(1)
