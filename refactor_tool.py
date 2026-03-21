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



def parse_clang_ast(input_file):
    """
    Parse the input C++ file using Clang to extract functions, variables, and classes.
    """
    index = clang.cindex.Index.create()
    try:
        # Preprocessing with GCC might have added some complexity, let's try parsing directly
        translation_unit = index.parse(
            str(input_file),
            args=['-x', 'c++', '-std=c++17'],
            options=clang.cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
        )
        for diagnostic in translation_unit.diagnostics:
            logging.warning(f"Clang Diagnostic: {diagnostic}")
    except Exception as e:
        logging.error(f"Failed to parse file: {e}")
        exit(1)

    functions = []
    variables = []
    classes = []
    includes = []

    def extract_declarations(node):
        """
        Recursively traverse the AST nodes to find function, variable, and class declarations.
        """
        # Only consider nodes from the main file, not from included headers (after preprocessing they might be there though)
        # But if we want to extract from the "spaghetti code" we probably want the ones defined in the input_file.
        if node.location.file and node.location.file.name != str(input_file):
             # We still might want to traverse namespaces defined in the file
             # but containing things from other files? Unlikely for this use case.
             return

        if node.kind == clang.cindex.CursorKind.FUNCTION_DECL or \
           node.kind == clang.cindex.CursorKind.CXX_METHOD:
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
        elif node.kind == clang.cindex.CursorKind.INCLUSION_DIRECTIVE:
            includes.append(node)
        elif node.kind == clang.cindex.CursorKind.NAMESPACE:
            # We don't want to extract the namespace definition itself as an item,
            # but we need to traverse into it.
            pass

        # Recursively visit child nodes
        for child in node.get_children():
            extract_declarations(child)

    extract_declarations(translation_unit.cursor)
    return functions, variables, classes, includes


def extract_code_from_node(node):
    """
    Extract the source code for a given node (function, variable, or class).
    """
    start = node.extent.start
    end = node.extent.end
    try:
        with open(start.file.name, 'r') as f:
            lines = f.readlines()

        # In Clang's extent, 'end' is usually the beginning of the last token.
        # We need to adjust to include the whole token or just use a simpler line-based approach
        # if the tokens are tricky.

        if start.line == end.line:
            return lines[start.line - 1][start.column - 1 : end.column].strip()

        res = []
        res.append(lines[start.line - 1][start.column - 1 :])
        for i in range(start.line, end.line - 1):
            res.append(lines[i])
        # We try to find the actual end of the token on the last line.
        # Often the end.column is the *start* of the last token.
        last_line = lines[end.line - 1]
        last_line_part = last_line[: end.column]
        # We scan forward for closing braces or semicolons.
        remaining = last_line[end.column:]
        for char in remaining:
            if char in '};':
                last_line_part += char
                break
            if char not in ' \t\n\r':
                break
        res.append(last_line_part)

        code = "".join(res).strip()

        # Add a semicolon if it's a class/struct/var and it's missing
        needs_semicolon = node.kind in [
            clang.cindex.CursorKind.CLASS_DECL,
            clang.cindex.CursorKind.STRUCT_DECL,
            clang.cindex.CursorKind.VAR_DECL,
            clang.cindex.CursorKind.FIELD_DECL
        ]
        if needs_semicolon and not code.endswith(';'):
            code += ';'

        return code

    except FileNotFoundError:
        logging.error(f"Source file {start.file.name} not found.")
        return ''
    except Exception as e:
        logging.error(f"Error extracting code for {node.spelling}: {e}")
        return ''


def get_full_name(node):
    """
    Returns the full name including namespaces/classes.
    """
    parts = []
    curr = node
    while curr and curr.kind not in [clang.cindex.CursorKind.TRANSLATION_UNIT, clang.cindex.CursorKind.INVALID_FILE]:
        if curr.spelling:
            parts.append(curr.spelling)
        curr = curr.semantic_parent
    return "::".join(reversed(parts))

def format_function_signature(func):
    """
    Format the function signature for declaration in the header file.
    """
    try:
        # Clang doesn't provide the exact signature text easily,
        # so we get the extent up to the body.
        start = func.extent.start
        # We look for the start of the body
        body = None
        for child in func.get_children():
            if child.kind == clang.cindex.CursorKind.COMPOUND_STMT:
                body = child
                break

        if body:
            end = body.extent.start
            with open(start.file.name, 'r') as f:
                lines = f.readlines()

            if start.line == end.line:
                sig = lines[start.line - 1][start.column - 1 : end.column - 1].strip()
            else:
                res = []
                res.append(lines[start.line - 1][start.column - 1 :])
                for i in range(start.line, end.line - 1):
                    res.append(lines[i])
                res.append(lines[end.line - 1][: end.column - 1])
                sig = "".join(res).strip()

            # If it's a method defined out-of-line, the signature in header
            # shouldn't have the class prefix.
            if func.kind == clang.cindex.CursorKind.CXX_METHOD:
                if "::" in sig:
                    # Very naive removal of class prefix
                    parts = sig.split("::")
                    # We want to keep everything before the first :: that is not the class name?
                    # Actually, we should just use the function spelling and return type.
                    # But the signature can have many things (const, virtual, etc.)
                    pass

            return sig + ";"

        return func.type.spelling + " " + func.spelling + ";" # Fallback
    except Exception as e:
        logging.error(f"Error formatting function signature for {func.spelling}: {e}")
        return ''


def get_namespace_path(node):
    """
    Returns list of namespaces.
    """
    parts = []
    curr = node.semantic_parent
    while curr and curr.kind == clang.cindex.CursorKind.NAMESPACE:
        if curr.spelling:
            parts.append(curr.spelling)
        curr = curr.semantic_parent
    return list(reversed(parts))

def wrap_in_namespaces(code, namespaces):
    """
    Wraps the code in namespace blocks.
    """
    if not namespaces:
        return code
    res = []
    for ns in namespaces:
        res.append(f"namespace {ns} {{")
    res.append(code)
    for ns in reversed(namespaces):
        res.append(f"}} // namespace {ns}")
    return "\n".join(res)

def generate_cpp_header_and_implementation(output_dir, functions, variables, classes, includes, target_names=None):
    """
    Generate the header (.h) and implementation (.cpp) files for extracted functions, variables, and classes.
    """
    header_file = output_dir / 'extracted_code.h'
    cpp_file = output_dir / 'extracted_code.cpp'

    # target_names can be USR strings to handle overloads
    def is_selected(item):
        if target_names is None:
            return True
        return item.get_usr() in target_names or item.spelling in target_names

    selected_functions = [f for f in functions if is_selected(f)]
    selected_variables = [v for v in variables if is_selected(v)]
    selected_classes = [c for c in classes if is_selected(c)]

    with open(header_file, 'w') as hf:
        hf.write("#ifndef EXTRACTED_CODE_H\n#define EXTRACTED_CODE_H\n\n")

        if includes:
            hf.write("// Extracted Includes\n")
            for inc in includes:
                try:
                    line = open(inc.location.file.name).readlines()[inc.location.line - 1]
                    if '"' in line:
                        hf.write(f'#include "{inc.displayname}"\n')
                    else:
                        hf.write(f'#include <{inc.displayname}>\n')
                except:
                    hf.write(f'#include <{inc.displayname}>\n')
            hf.write("\n")

        hf.write("// Declarations of extracted functions\n")
        for func in selected_functions:
            # For methods defined out of line, we don't want to redeclare them in header
            # as they are part of the class.
            if func.kind == clang.cindex.CursorKind.CXX_METHOD:
                # If it's a method, it should be in the class definition.
                # However, if we only extract the method and not the class,
                # we are in trouble anyway.
                # Let's skip redeclaring methods in the header.
                continue
            signature = format_function_signature(func)
            ns = get_namespace_path(func)
            hf.write(wrap_in_namespaces(signature, ns) + '\n\n')

        hf.write("\n// Declarations of extracted variables\n")
        for var in selected_variables:
            ns = get_namespace_path(var)
            hf.write(wrap_in_namespaces(f"extern {var.type.spelling} {var.spelling};", ns) + '\n\n')

        hf.write("\n// Definitions of extracted classes\n")
        for cls in selected_classes:
            class_code = extract_code_from_node(cls)
            if class_code:
                ns = get_namespace_path(cls)
                hf.write(wrap_in_namespaces(class_code, ns) + '\n\n')

        hf.write("\n#endif // EXTRACTED_CODE_H\n")

    with open(cpp_file, 'w') as cf:
        cf.write('#include "extracted_code.h"\n\n')
        cf.write("// Implementations of extracted functions\n")
        for func in selected_functions:
            func_code = extract_code_from_node(func)
            if func_code:
                # If it's a method defined out of line, it's already scoped.
                # If not, we might need namespace wrap.
                ns = get_namespace_path(func)
                # Naive check for out-of-line: spelled with ::
                # But func.spelling only gives the name, not the full name.
                # We need to check if the code contains :: before the body.
                is_out_of_line = "::" in func_code.split('{')[0]
                if not is_out_of_line and ns:
                    func_code = wrap_in_namespaces(func_code, ns)
                cf.write(func_code + '\n\n')

        cf.write("\n// Definitions of extracted variables\n")
        for var in selected_variables:
            var_code = extract_code_from_node(var)
            if var_code:
                ns = get_namespace_path(var)
                if ns:
                    var_code = wrap_in_namespaces(var_code, ns)
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

    # Step 1: Parse AST using Clang
    # We parse the original file because parsing the preprocessed file
    # makes Clang think all definitions are in the .i file, which complicates
    # extracting from the correct source.
    functions, variables, classes, includes = parse_clang_ast(input_path)

    if target_names is None:
        # If no target names, just list what we found
        logging.info("Found functions: " + ", ".join([f.spelling for f in functions]))
        logging.info("Found variables: " + ", ".join([v.spelling for v in variables]))
        logging.info("Found classes: " + ", ".join([c.spelling for c in classes]))
        return functions, variables, classes

    # Step 3: Generate header and implementation files
    generate_cpp_header_and_implementation(output_path, functions, variables, classes, includes, target_names)
    return functions, variables, classes

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="C++ Code Extraction and Refactoring Tool")
    parser.add_argument("input_file", type=str, help="Path to the input C++ source file.")
    parser.add_argument("-o", "--output_dir", type=str, default="./output", help="Output directory.")
    parser.add_argument("-t", "--targets", type=str, nargs='*', help="Specific names to extract.")
    args = parser.parse_args()

    main(args.input_file, args.output_dir, args.targets)
