import clang.cindex
import os
from pathlib import Path
import argparse
import subprocess
import logging

# Configure logging for better debugging and user feedback
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# We'll use a more robust search for libclang
def find_libclang():
    # Standard locations for different distributions and LLVM versions
    search_dirs = [
        '/usr/lib/x86_64-linux-gnu',
        '/usr/lib/llvm-18/lib',
        '/usr/lib/llvm-17/lib',
        '/usr/lib/llvm-16/lib',
        '/usr/lib/llvm-15/lib',
        '/usr/lib/llvm-14/lib',
        '/usr/lib',
        '/usr/local/lib',
    ]
    possible_names = [
        'libclang.so',
        'libclang.so.1',
        'libclang-18.so.1',
        'libclang-17.so.1',
        'libclang-16.so.1',
        'libclang-15.so.1',
        'libclang-14.so.1',
    ]
    for d in search_dirs:
        for name in possible_names:
            file_path = os.path.join(d, name)
            if os.path.exists(file_path):
                return file_path
    return None

libclang_file = find_libclang()
if libclang_file:
    clang.cindex.Config.set_library_file(libclang_file)

def run_gcc_preprocessor(input_file):
    """
    Preprocess the input C++ file using GCC preprocessor to expand macros and includes.
    """
    preprocessed_file = input_file.with_suffix('.i')
    cmd = ['g++', '-E', str(input_file), '-o', str(preprocessed_file)]
    try:
        subprocess.run(cmd, check=True)
        logging.info(f"Preprocessed file generated: {preprocessed_file}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Preprocessing failed: {e}")
        exit(1)
    return preprocessed_file


def parse_clang_ast(input_file):
    """
    Parse the input C++ file using Clang to extract functions, variables, and classes.
    """
    index = clang.cindex.Index.create()
    try:
        # Preprocessing with GCC might have added some complexity, let's try parsing directly
        translation_unit = index.parse(
            str(input_file), args=['-x', 'c++', '-std=c++17']
        )
        for diagnostic in translation_unit.diagnostics:
            logging.warning(f"Clang Diagnostic: {diagnostic}")
    except Exception as e:
        logging.error(f"Failed to parse file: {e}")
        exit(1)

    functions = []
    variables = []
    classes = []

    def extract_declarations(node):
        """
        Recursively traverse the AST nodes to find function, variable, and class declarations.
        """
        # Only consider nodes from the main file, not from included headers (after preprocessing they might be there though)
        # But if we want to extract from the "spaghetti code" we probably want the ones defined in the input_file.
        if node.location.file and node.location.file.name != str(input_file):
             return

        if node.kind == clang.cindex.CursorKind.FUNCTION_DECL:
            if node.is_definition():
                functions.append(node)

        elif node.kind == clang.cindex.CursorKind.VAR_DECL:
            # We might want only global variables
            if node.semantic_parent.kind == clang.cindex.CursorKind.TRANSLATION_UNIT:
                variables.append(node)

        elif node.kind == clang.cindex.CursorKind.CLASS_DECL and node.is_definition():
            classes.append(node)
        elif node.kind == clang.cindex.CursorKind.STRUCT_DECL and node.is_definition():
            classes.append(node)

        # Recursively visit child nodes
        for child in node.get_children():
            extract_declarations(child)

    extract_declarations(translation_unit.cursor)
    return functions, variables, classes


def extract_code_from_node(node):
    """
    Extract the source code for a given node (function, variable, or class).
    """
    start = node.extent.start
    end = node.extent.end
    try:
        with open(start.file.name, 'r') as f:
            lines = f.readlines()

        if start.line == end.line:
            return lines[start.line - 1][start.column - 1 : end.column - 1]

        res = []
        res.append(lines[start.line - 1][start.column - 1 :])
        for i in range(start.line, end.line - 1):
            res.append(lines[i])
        res.append(lines[end.line - 1][: end.column - 1])
        # Add a semicolon if it's a class or struct and it's missing
        code = "".join(res)
        if node.kind in [clang.cindex.CursorKind.CLASS_DECL, clang.cindex.CursorKind.STRUCT_DECL]:
            if not code.strip().endswith(';'):
                code = code.rstrip() + ';'
        return code

    except FileNotFoundError:
        logging.error(f"Source file {start.file.name} not found.")
        return ''
    except Exception as e:
        logging.error(f"Error extracting code for {node.spelling}: {e}")
        return ''


def format_function_signature(func):
    """
    Format the function signature for declaration in the header file.
    """
    try:
        # This is a bit naive, but let's try to get the signature before the body
        start = func.extent.start
        # We find the first '{' to stop the signature
        with open(start.file.name, 'r') as f:
            lines = f.readlines()

        signature_lines = []
        found_brace = False
        for i in range(start.line - 1, len(lines)):
            line = lines[i]
            if i == start.line - 1:
                line = line[start.column - 1:]

            if '{' in line:
                signature_lines.append(line.split('{')[0])
                found_brace = True
                break
            else:
                signature_lines.append(line)

        if found_brace:
            return "".join(signature_lines).strip() + ";"
        return func.type.spelling + " " + func.spelling + ";" # Fallback
    except Exception as e:
        logging.error(f"Error formatting function signature for {func.spelling}: {e}")
        return ''


def generate_cpp_header_and_implementation(output_dir, functions, variables, classes, target_names=None):
    """
    Generate the header (.h) and implementation (.cpp) files for extracted functions, variables, and classes.
    """
    header_file = output_dir / 'extracted_code.h'
    cpp_file = output_dir / 'extracted_code.cpp'

    selected_functions = [f for f in functions if target_names is None or f.spelling in target_names]
    selected_variables = [v for v in variables if target_names is None or v.spelling in target_names]
    selected_classes = [c for c in classes if target_names is None or c.spelling in target_names]

    with open(header_file, 'w') as hf:
        hf.write("#ifndef EXTRACTED_CODE_H\n#define EXTRACTED_CODE_H\n\n")
        hf.write("// Declarations of extracted functions\n")
        for func in selected_functions:
            signature = format_function_signature(func)
            hf.write(signature + '\n')

        hf.write("\n// Declarations of extracted variables\n")
        for var in selected_variables:
            hf.write(f"extern {var.type.spelling} {var.spelling};\n")

        hf.write("\n// Definitions of extracted classes\n")
        for cls in selected_classes:
            class_code = extract_code_from_node(cls)
            if class_code:
                hf.write(class_code + '\n\n')

        hf.write("\n#endif // EXTRACTED_CODE_H\n")

    with open(cpp_file, 'w') as cf:
        cf.write('#include "extracted_code.h"\n\n')
        cf.write("// Implementations of extracted functions\n")
        for func in selected_functions:
            func_code = extract_code_from_node(func)
            if func_code:
                cf.write(func_code + '\n\n')

        cf.write("\n// Definitions of extracted variables\n")
        for var in selected_variables:
            var_code = extract_code_from_node(var)
            if var_code:
                cf.write(var_code + '\n')

    logging.info(f"Generated header file: {header_file}")
    logging.info(f"Generated implementation file: {cpp_file}")


def main(input_file, output_dir, target_names=None):
    """
    Main function to execute the code extraction process.
    """
    input_path = Path(input_file)
    output_path = Path(output_dir)

    if not input_path.exists():
        logging.error(f"Input file {input_path} does not exist.")
        return

    output_path.mkdir(exist_ok=True)

    # Step 1: Preprocess the code
    preprocessed_file = run_gcc_preprocessor(input_path)

    # Step 2: Parse AST using Clang
    functions, variables, classes = parse_clang_ast(input_path)

    if target_names is None:
        # If no target names, just list what we found
        logging.info("Found functions: " + ", ".join([f.spelling for f in functions]))
        logging.info("Found variables: " + ", ".join([v.spelling for v in variables]))
        logging.info("Found classes: " + ", ".join([c.spelling for c in classes]))
        return functions, variables, classes

    # Step 3: Generate header and implementation files
    generate_cpp_header_and_implementation(output_path, functions, variables, classes, target_names)
    return functions, variables, classes

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="C++ Code Extraction and Refactoring Tool")
    parser.add_argument("input_file", type=str, help="Path to the input C++ source file.")
    parser.add_argument("-o", "--output_dir", type=str, default="./output", help="Output directory.")
    parser.add_argument("-t", "--targets", type=str, nargs='*', help="Specific names to extract.")
    args = parser.parse_args()

    main(args.input_file, args.output_dir, args.targets)
