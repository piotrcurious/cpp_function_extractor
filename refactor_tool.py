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

    # We can try to find the system include paths to avoid crashes with <iostream> etc.
    args = ['-x', 'c++', '-std=c++17', '-D__CODE_GENERATOR__']

    # Try to get system include paths from g++
    try:
        proc = subprocess.run(['g++', '-E', '-x', 'c++', '-', '-v'],
                              input='', capture_output=True, text=True)
        in_includes = False
        for line in proc.stderr.splitlines():
            if '#include <...> search starts here:' in line:
                in_includes = True
                continue
            if 'End of search list.' in line:
                in_includes = False
                continue
            if in_includes:
                path = line.strip()
                if os.path.exists(path):
                    args.extend(["-isystem", path])
    except Exception as e:
        logging.warning(f"Could not determine system include paths from g++: {e}")

    try:
        # Preprocessing with GCC might have added some complexity, let's try parsing directly
        # If it crashes, we try again with a very minimal set of arguments
        try:
            translation_unit = index.parse(
                str(input_file),
                args=args,
                options=clang.cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
            )
        except Exception:
             translation_unit = index.parse(
                str(input_file),
                args=['-x', 'c++', '-std=c++17', '-D__CODE_GENERATOR__'],
                options=0
            )
        for diagnostic in translation_unit.diagnostics:
            if diagnostic.severity >= clang.cindex.Diagnostic.Error:
                logging.error(f"Clang Diagnostic: {diagnostic}")
            else:
                logging.warning(f"Clang Diagnostic: {diagnostic}")
    except Exception as e:
        logging.error(f"Failed to parse file: {e}")
        return [], [], [], []

    functions = []
    variables = []
    classes = []
    includes = []

    def extract_declarations(node):
        """
        Recursively traverse the AST nodes to find function, variable, and class declarations.
        """
        if node.location.file and node.location.file.name != str(input_file):
             return

        if node.kind == clang.cindex.CursorKind.FUNCTION_DECL or \
           node.kind == clang.cindex.CursorKind.CXX_METHOD:
            if node.is_definition():
                functions.append(node)

        elif node.kind == clang.cindex.CursorKind.VAR_DECL:
            # We want global variables OR static class members
            is_global = node.semantic_parent.kind == clang.cindex.CursorKind.TRANSLATION_UNIT
            is_static_member = node.semantic_parent.kind in [clang.cindex.CursorKind.CLASS_DECL,
                                                            clang.cindex.CursorKind.STRUCT_DECL,
                                                            clang.cindex.CursorKind.CLASS_TEMPLATE]
            if is_global or is_static_member:
                variables.append(node)

        elif node.kind in [clang.cindex.CursorKind.CLASS_DECL,
                           clang.cindex.CursorKind.STRUCT_DECL,
                           clang.cindex.CursorKind.CLASS_TEMPLATE] and node.is_definition():
            classes.append(node)
        elif node.kind == clang.cindex.CursorKind.INCLUSION_DIRECTIVE:
            includes.append(node)

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

        if start.line == end.line:
            return lines[start.line - 1][start.column - 1 : end.column].strip()

        res = []
        res.append(lines[start.line - 1][start.column - 1 :])
        for i in range(start.line, end.line - 1):
            res.append(lines[i])

        last_line = lines[end.line - 1]

        # Improved token scanning
        tokens = list(node.get_tokens())
        if tokens:
            last_token = tokens[-1]
            end_loc = last_token.extent.end
            res = []
            if start.line == end_loc.line:
                code = lines[start.line - 1][start.column - 1 : end_loc.column - 1].strip()
            else:
                res.append(lines[start.line - 1][start.column - 1 :])
                for i in range(start.line, end_loc.line - 1):
                    res.append(lines[i])
                res.append(lines[end_loc.line - 1][: end_loc.column - 1])
                code = "".join(res).strip()
        else:
            last_line_part = last_line[: end.column]
            remaining = last_line[end.column - 1:]
            for char in remaining:
                if char in '};':
                    last_line_part += char
                    break
                if char not in ' \t\n\r':
                    # If we see another character before } or ;, we probably shouldn't append it
                    # but for safety we'll stop scanning to avoid over-consuming.
                    break
            res.append(last_line_part)
            code = "".join(res).strip()

        needs_semicolon = node.kind in [
            clang.cindex.CursorKind.CLASS_DECL,
            clang.cindex.CursorKind.STRUCT_DECL,
            clang.cindex.CursorKind.CLASS_TEMPLATE,
            clang.cindex.CursorKind.VAR_DECL,
            clang.cindex.CursorKind.FIELD_DECL
        ]
        if needs_semicolon and not code.endswith(';'):
            code += ';'

        return code

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
        # Use tokens for more precise extraction
        tokens = list(func.get_tokens())
        if not tokens:
             return func.type.spelling + " " + func.spelling + ";" # Fallback

        # We want the tokens before the body starts
        body_start = None
        for child in func.get_children():
            if child.kind == clang.cindex.CursorKind.COMPOUND_STMT:
                body_start = child.extent.start
                break

        sig_tokens = []
        for token in tokens:
            if body_start and token.extent.start.line >= body_start.line and \
               token.extent.start.column >= body_start.column:
                break
            # Skip the body and also any attributes that might be attached to the body?
            # Usually we just stop at '{'
            if token.spelling == '{':
                break
            sig_tokens.append(token.spelling)

        if sig_tokens:
            # Join tokens with spaces, then clean up some common C++ spacing issues
            sig = " ".join(sig_tokens)
            sig = sig.replace(" (", "(").replace("( ", "(").replace(" )", ")").replace(" *", "*").replace(" &", "&")
            sig = sig.replace(" ,", ",").replace(" :", ":").replace(":: ", "::").replace(" ::", "::")
            return sig.strip() + ";"

        # Fallback to older method if token approach fails
        start = func.extent.start
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
    while curr and curr.kind != clang.cindex.CursorKind.TRANSLATION_UNIT:
        if curr.kind == clang.cindex.CursorKind.NAMESPACE:
            if curr.spelling:
                parts.append(curr.spelling)
        curr = curr.semantic_parent
    return list(reversed(parts))

def get_full_namespace_and_class_path(node):
    """
    Returns list of namespaces and classes.
    """
    parts = []
    curr = node.semantic_parent
    while curr and curr.kind != clang.cindex.CursorKind.TRANSLATION_UNIT:
        if curr.kind in [clang.cindex.CursorKind.NAMESPACE,
                         clang.cindex.CursorKind.CLASS_DECL,
                         clang.cindex.CursorKind.STRUCT_DECL,
                         clang.cindex.CursorKind.CLASS_TEMPLATE]:
            if curr.spelling:
                parts.append((curr.kind, curr.spelling))
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

    def is_selected(item):
        if target_names is None:
            return True
        if item.get_usr() in target_names or item.spelling in target_names:
            return True
        full_name = get_full_name(item)
        if full_name in target_names:
            return True
        return False

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

        grouped_items = {}

        for func in selected_functions:
            if func.kind == clang.cindex.CursorKind.CXX_METHOD:
                curr = func.semantic_parent
                already_in_header = False
                while curr and curr.kind != clang.cindex.CursorKind.TRANSLATION_UNIT:
                    if is_selected(curr):
                        already_in_header = True
                        break
                    curr = curr.semantic_parent
                if already_in_header:
                    continue

            path = tuple(get_full_namespace_and_class_path(func))
            if path not in grouped_items:
                grouped_items[path] = []

            signature = format_function_signature(func)
            if signature:
                # Improved un-qualification for methods in headers
                if func.kind == clang.cindex.CursorKind.CXX_METHOD:
                    # Remove any qualification of the method name itself
                    # We know the method name is func.spelling
                    # We want to replace 'Anything::method_name' with 'method_name'
                    # but only when it refers to the method being declared.
                    # It's safer to use the tokens if we can, but let's try a better string approach.
                    if "::" + func.spelling in signature:
                         # Find the method name and strip qualification before it
                         idx = signature.rfind("::" + func.spelling)
                         # We want to find where the qualification starts
                         start_idx = idx
                         while start_idx > 0 and (signature[start_idx-1].isalnum() or signature[start_idx-1] in '_:'):
                             start_idx -= 1
                         signature = signature[:start_idx] + signature[idx+2:]
                grouped_items[path].append(signature)

        for var in selected_variables:
            curr = var.semantic_parent
            already_in_header = False
            while curr and curr.kind != clang.cindex.CursorKind.TRANSLATION_UNIT:
                if is_selected(curr):
                    already_in_header = True
                    break
                curr = curr.semantic_parent
            if already_in_header:
                continue

            path = tuple(get_full_namespace_and_class_path(var))
            if path not in grouped_items:
                grouped_items[path] = []

            # For static class members, we don't want 'extern' in the class definition
            item = f"{var.type.spelling} {var.spelling};"
            if var.semantic_parent.kind in [clang.cindex.CursorKind.CLASS_DECL,
                                            clang.cindex.CursorKind.STRUCT_DECL,
                                            clang.cindex.CursorKind.CLASS_TEMPLATE]:
                item = f"static {item}"
            else:
                item = f"extern {item}"

            if item not in grouped_items[path]:
                grouped_items[path].append(item)

        def emit_grouped_items(items_dict):
            tree = {}
            for path, items in items_dict.items():
                curr = tree
                for p in path:
                    if p not in curr:
                        curr[p] = {'_items': [], '_children': {}}
                    curr = curr[p]['_children']

                curr = tree
                for p in path[:-1]:
                    curr = curr[p]['_children']
                if path:
                    curr[path[-1]]['_items'].extend(items)
                else:
                    if None not in tree:
                        tree[None] = {'_items': [], '_children': {}}
                    tree[None]['_items'].extend(items)

            def print_tree(node_tree, indent=""):
                res = []
                if None in node_tree:
                    for item in node_tree[None]['_items']:
                        res.append(f"{indent}{item}")
                    del node_tree[None]

                for p, data in sorted(node_tree.items(), key=lambda x: x[0][1]):
                    kind, name = p
                    if kind == clang.cindex.CursorKind.NAMESPACE:
                        res.append(f"{indent}namespace {name} {{")
                        res.extend(print_tree(data['_children'], indent + "    "))
                        for item in data['_items']:
                            res.append(f"{indent}    {item}")
                        res.append(f"{indent}}} // namespace {name}")
                    else:
                        k = "class"
                        if kind == clang.cindex.CursorKind.STRUCT_DECL:
                            k = "struct"
                        elif kind == clang.cindex.CursorKind.CLASS_TEMPLATE:
                            # We don't have the template parameters easily,
                            # but we can try to guess from tokens of one of the items?
                            # For now, just a placeholder.
                            res.append(f"{indent}template <typename T>")
                            k = "class"
                        res.append(f"{indent}{k} {name} {{")
                        res.append(f"{indent}public:")
                        res.extend(print_tree(data['_children'], indent + "    "))
                        for item in data['_items']:
                            res.append(f"{indent}    {item}")
                        res.append(f"{indent}}};")
                return res

            return "\n".join(print_tree(tree))

        hf.write("// Extracted Declarations\n")
        hf.write(emit_grouped_items(grouped_items) + "\n\n")

        hf.write("\n// Definitions of extracted classes\n")
        for cls in selected_classes:
            class_code = extract_code_from_node(cls)
            if class_code:
                ns = get_namespace_path(cls)
                hf.write(wrap_in_namespaces(class_code, ns) + '\n\n')

        hf.write("\n#endif // EXTRACTED_CODE_H\n")

    with open(cpp_file, 'w') as cf:
        cf.write('#include "extracted_code.h"\n\n')

        # Helper to avoid wrapping if it's already a template
        def wrap_template_if_needed(node, code):
            # Check if any parent is a template
            curr = node.semantic_parent
            template_params = []
            while curr and curr.kind != clang.cindex.CursorKind.TRANSLATION_UNIT:
                if curr.kind == clang.cindex.CursorKind.CLASS_TEMPLATE:
                    # Try to extract template parameters
                    # This is complex, but let's try a simple approach
                    pass
                curr = curr.semantic_parent
            return code

        cf.write("// Implementations of extracted functions\n")
        for func in selected_functions:
            if func.kind == clang.cindex.CursorKind.CXX_METHOD:
                curr = func.semantic_parent
                skip = False
                # We only skip if the method was defined INSIDE the class definition
                # and the class is also selected.
                # If it was defined out-of-line, we still want to extract it to .cpp.
                is_out_of_line = func.lexical_parent != func.semantic_parent

                if not is_out_of_line:
                    while curr and curr.kind != clang.cindex.CursorKind.TRANSLATION_UNIT:
                        if is_selected(curr):
                            skip = True
                            break
                        curr = curr.semantic_parent
                if skip:
                    continue

            func_code = extract_code_from_node(func)
            if func_code:
                ns = get_namespace_path(func)
                is_out_of_line = func.lexical_parent != func.semantic_parent

                if func.kind == clang.cindex.CursorKind.CXX_METHOD and not is_out_of_line:
                    class_name = func.semantic_parent.spelling
                    # We need to handle nested classes here too for correct scoping
                    full_class_path = []
                    curr_p = func.semantic_parent
                    while curr_p and curr_p.kind in [clang.cindex.CursorKind.CLASS_DECL, clang.cindex.CursorKind.STRUCT_DECL]:
                        full_class_path.append(curr_p.spelling)
                        curr_p = curr_p.semantic_parent
                    full_class_qualifier = "::".join(reversed(full_class_path))

                    if '{' in func_code:
                        prefix, body = func_code.split('{', 1)
                        if func.spelling in prefix:
                             # Use tokens to find the actual function name to avoid false positives in return type or parameters
                             tokens = list(func.get_tokens())
                             func_name_token = None
                             for t in tokens:
                                 if t.spelling == func.spelling and t.extent.start.line == func.location.line:
                                     # This is likely the function name
                                     func_name_token = t
                                     break

                             if func_name_token:
                                 # We re-construct the prefix using the token information
                                 last_idx = prefix.rfind(func.spelling) # Simplified fallback
                                 new_prefix = prefix[:last_idx] + full_class_qualifier + "::" + prefix[last_idx:]
                                 func_code = new_prefix + '{' + body
                             else:
                                 last_idx = prefix.rfind(func.spelling)
                                 new_prefix = prefix[:last_idx] + full_class_qualifier + "::" + prefix[last_idx:]
                                 func_code = new_prefix + '{' + body

                if ns:
                    already_wrapped = False
                    # Check if it starts with template <...> namespace or just namespace
                    stripped_code = func_code.strip()
                    if stripped_code.startswith("template"):
                        # Skip template part
                        if "namespace" in stripped_code:
                             # This is very simplified
                             pass

                    for n in ns:
                        if f"namespace {n}" in func_code:
                            already_wrapped = True
                            break

                    if not already_wrapped:
                         first_ns = ns[0]
                         if func_code.split('{')[0].strip().startswith(f"{first_ns}::") or \
                            f" {first_ns}::" in func_code.split('{')[0]:
                             already_wrapped = True

                    if not already_wrapped:
                        func_code = wrap_in_namespaces(func_code, ns)

                cf.write(func_code + '\n\n')

        cf.write("\n// Definitions of extracted variables\n")
        for var in selected_variables:
            # If it's a static member of a class template, it usually goes in the header
            # OR it must be defined in the .cpp if it's not a template.
            # However, if it's already in the source as a definition, we extract it.

            # Static members in class are just declarations.
            # Definitions are separate.
            if var.kind == clang.cindex.CursorKind.VAR_DECL and var.is_definition():
                var_code = extract_code_from_node(var)
                if var_code:
                    ns = get_namespace_path(var)
                    if ns:
                        already_wrapped = False
                        for n in ns:
                            if f"namespace {n}" in var_code:
                                already_wrapped = True
                                break
                        if not already_wrapped:
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
        return [], [], []

    output_path.mkdir(exist_ok=True)
    functions, variables, classes, includes = parse_clang_ast(input_path)

    if target_names is None:
        logging.info("Found functions: " + ", ".join([f.spelling for f in functions]))
        logging.info("Found variables: " + ", ".join([v.spelling for v in variables]))
        logging.info("Found classes: " + ", ".join([c.spelling for c in classes]))
        return functions, variables, classes

    generate_cpp_header_and_implementation(output_path, functions, variables, classes, includes, target_names)
    return functions, variables, classes

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="C++ Code Extraction and Refactoring Tool")
    parser.add_argument("input_file", type=str, help="Path to the input C++ source file.")
    parser.add_argument("-o", "--output_dir", type=str, default="./output", help="Output directory.")
    parser.add_argument("-t", "--targets", type=str, nargs='*', help="Specific names to extract.")
    args = parser.parse_args()

    main(args.input_file, args.output_dir, args.targets)
