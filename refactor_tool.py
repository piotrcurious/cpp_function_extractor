import clang.cindex
import os
from pathlib import Path
import argparse
import subprocess
import logging

# Configure logging for better debugging and user feedback
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def find_libclang():
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
    index = clang.cindex.Index.create()
    args = ['-x', 'c++', '-std=c++20', '-D__CODE_GENERATOR__']

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
        return [], [], [], [], [], [], []

    functions, variables, classes, includes, enums, aliases, macros = [], [], [], [], [], [], []

    def extract_declarations(node):
        if node.location.file and node.location.file.name != str(input_file):
             return

        if node.kind in [clang.cindex.CursorKind.FUNCTION_DECL,
                         clang.cindex.CursorKind.CXX_METHOD,
                         clang.cindex.CursorKind.FUNCTION_TEMPLATE]:
            if node.is_definition():
                functions.append(node)
        elif node.kind == clang.cindex.CursorKind.VAR_DECL:
            is_global = node.semantic_parent.kind == clang.cindex.CursorKind.TRANSLATION_UNIT
            is_static_member = node.semantic_parent.kind in [clang.cindex.CursorKind.CLASS_DECL,
                                                            clang.cindex.CursorKind.STRUCT_DECL,
                                                            clang.cindex.CursorKind.CLASS_TEMPLATE]
            if is_global or is_static_member:
                variables.append(node)
        elif node.kind in [clang.cindex.CursorKind.CLASS_DECL, clang.cindex.CursorKind.STRUCT_DECL,
                           clang.cindex.CursorKind.UNION_DECL,
                           clang.cindex.CursorKind.CLASS_TEMPLATE] and node.is_definition():
            classes.append(node)
        elif node.kind == clang.cindex.CursorKind.ENUM_DECL and node.is_definition():
            enums.append(node)
        elif node.kind == clang.cindex.CursorKind.CONCEPT_DECL:
            enums.append(node) # Use enums list for simplicity or add to aliases? Classes might be better.
        elif node.kind in [clang.cindex.CursorKind.TYPEDEF_DECL,
                           clang.cindex.CursorKind.TYPE_ALIAS_DECL,
                           clang.cindex.CursorKind.NAMESPACE_ALIAS]:
            aliases.append(node)
        elif node.kind == clang.cindex.CursorKind.INCLUSION_DIRECTIVE:
            includes.append(node)
        elif node.kind == clang.cindex.CursorKind.MACRO_DEFINITION:
            if node.location.file and node.location.file.name == str(input_file):
                macros.append(node)

        for child in node.get_children():
            extract_declarations(child)

    extract_declarations(translation_unit.cursor)
    return functions, variables, classes, includes, enums, aliases, macros


def extract_code_from_node(node, include_comments=True):
    start = node.extent.start
    end = node.extent.end
    try:
        with open(start.file.name, 'r') as f:
            lines = f.readlines()

        tokens = list(node.get_tokens())

        # Capture leading comments if requested
        if include_comments:
            # Look at tokens before the extent start in the same file
            # This is tricky using node.get_tokens(). We should use TU tokens.
            tu = node.translation_unit
            # Get tokens for the line(s) before
            if start.line > 1:
                comment_search_start = clang.cindex.SourceLocation.from_position(tu, start.file, max(1, start.line - 5), 1)
                comment_search_end = start
                pre_tokens = list(tu.get_tokens(extent=clang.cindex.SourceRange.from_locations(comment_search_start, comment_search_end)))

                leading_comments = []
                for t in reversed(pre_tokens):
                    if t.kind == clang.cindex.TokenKind.COMMENT:
                        leading_comments.append(t.spelling)
                    elif t.spelling in [';', '}', '{']: # Stop at previous declaration end
                        break
                    elif t.kind != clang.cindex.TokenKind.PUNCTUATION: # Some other token
                         # If it's on the same line as the start, it might be an attribute, handled elsewhere
                         pass

                if leading_comments:
                    # They are in reverse order
                    comments_text = "\n".join(reversed(leading_comments))
                else:
                    comments_text = ""
            else:
                comments_text = ""

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

            if include_comments and comments_text:
                code = comments_text + "\n" + code
        else:
            if start.line == end.line:
                return lines[start.line - 1][start.column - 1 : end.column].strip()
            res = [lines[start.line - 1][start.column - 1 :]]
            for i in range(start.line, end.line - 1):
                res.append(lines[i])
            last_line = lines[end.line - 1]
            last_line_part = last_line[: end.column]
            remaining = last_line[end.column:]
            for char in remaining:
                if char in '};':
                    last_line_part += char
                    break
                if char not in ' \t\n\r':
                    break
            res.append(last_line_part)
            code = "".join(res).strip()

        needs_semicolon = node.kind in [
            clang.cindex.CursorKind.CLASS_DECL, clang.cindex.CursorKind.STRUCT_DECL,
            clang.cindex.CursorKind.UNION_DECL,
            clang.cindex.CursorKind.CLASS_TEMPLATE, clang.cindex.CursorKind.VAR_DECL,
            clang.cindex.CursorKind.FIELD_DECL, clang.cindex.CursorKind.ENUM_DECL,
            clang.cindex.CursorKind.TYPEDEF_DECL, clang.cindex.CursorKind.TYPE_ALIAS_DECL,
            clang.cindex.CursorKind.NAMESPACE_ALIAS, clang.cindex.CursorKind.CONCEPT_DECL
        ]
        if needs_semicolon and not code.endswith(';'):
            code += ';'
        return code
    except Exception as e:
        logging.error(f"Error extracting code for {node.spelling}: {e}")
        return ''


def get_full_name(node):
    parts = []
    curr = node
    while curr and curr.kind not in [clang.cindex.CursorKind.TRANSLATION_UNIT, clang.cindex.CursorKind.INVALID_FILE]:
        if curr.spelling:
            parts.append(curr.spelling)
        curr = curr.semantic_parent
    return "::".join(reversed(parts))

def format_function_signature(func):
    try:
        # Check for virtual/static keywords
        prefix_keywords = []
        if func.is_virtual_method():
            prefix_keywords.append("virtual")
        if func.is_static_method():
            prefix_keywords.append("static")

        # We try to get tokens from the lexical parent if necessary to capture attributes
        # but Clang is usually good enough if we know where to look.
        # Let's try to get tokens from the start of the extent, but sometimes attributes
        # are just before.

        # Experimental: get tokens from a bit before the extent start
        # We'll use the translation unit to get tokens in a range
        tu = func.translation_unit
        start_extent = func.extent.start
        # Look back some lines/columns to see if we find attributes
        file_name = start_extent.file.name

        # Try to find the start of the line or previous line
        # This is safer to avoid skipping [[attributes]]
        # We'll use get_extent with start and end locations
        search_start = clang.cindex.SourceLocation.from_position(tu, start_extent.file, start_extent.line, 1)
        search_end = func.extent.end
        tokens = list(tu.get_tokens(extent=clang.cindex.SourceRange.from_locations(search_start, search_end)))

        # If the first token is after the extent start, we might have missed something
        # but here the extent start is 9:15 and constexpr is at 9:15.
        # [[nodiscard]] is at 9:3.

        if not tokens:
             return func.type.spelling + " " + func.spelling + ";"

        body_start = None
        for child in func.get_children():
            if child.kind == clang.cindex.CursorKind.COMPOUND_STMT:
                body_start = child.extent.start
                break

        # We also need to check if there are tokens before the extent start
        # (Attributes like [[nodiscard]] might be before)

        sig_tokens = []
        for token in tokens:
            if body_start and (token.extent.start.line > body_start.line or \
               (token.extent.start.line == body_start.line and token.extent.start.column >= body_start.column)):
                break
            if token.spelling == '{':
                break
            sig_tokens.append(token.spelling)

        if sig_tokens:
            sig = " ".join(sig_tokens)
            # Refined C++ spacing cleanup
            sig = sig.replace(" (", "(").replace("( ", "(").replace(" )", ")")
            sig = sig.replace(" *", "*").replace(" &", "&")
            sig = sig.replace(" ,", ",").replace(" :", ":")
            sig = sig.replace(":: ", "::").replace(" ::", "::")
            sig = sig.replace("< ", "<").replace(" >", ">").replace(" <", "<")
            sig = sig.replace("[ [", "[[").replace(" ] ]", "]]").replace(" ]]", "]]").replace("[[ ", "[[")
            # C++20 concepts/constraints cleanup
            sig = sig.replace("template<", "template <")
            return sig.strip() + ";"

        return func.type.spelling + " " + func.spelling + ";"
    except Exception as e:
        logging.error(f"Error formatting function signature for {func.spelling}: {e}")
        return ''


def get_namespace_path(node):
    parts = []
    curr = node.semantic_parent
    while curr and curr.kind != clang.cindex.CursorKind.TRANSLATION_UNIT:
        if curr.kind == clang.cindex.CursorKind.NAMESPACE:
            if curr.spelling:
                parts.append(curr.spelling)
        curr = curr.semantic_parent
    return list(reversed(parts))

def get_full_namespace_and_class_path(node):
    parts = []
    curr = node.semantic_parent
    while curr and curr.kind != clang.cindex.CursorKind.TRANSLATION_UNIT:
        if curr.kind in [clang.cindex.CursorKind.NAMESPACE, clang.cindex.CursorKind.CLASS_DECL,
                         clang.cindex.CursorKind.STRUCT_DECL, clang.cindex.CursorKind.UNION_DECL,
                         clang.cindex.CursorKind.CLASS_TEMPLATE]:
            if curr.spelling:
                parts.append(curr)
        curr = curr.semantic_parent
    return list(reversed(parts))

def wrap_in_namespaces(code, namespaces):
    if not namespaces:
        return code
    res = []
    for ns in namespaces:
        res.append(f"namespace {ns} {{")
    res.append(code)
    for ns in reversed(namespaces):
        res.append(f"}} // namespace {ns}")
    return "\n".join(res)

def generate_cpp_header_and_implementation(output_dir, functions, variables, classes, includes, enums, aliases, macros, target_names=None):
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
    selected_enums = [e for e in enums if is_selected(e)]
    selected_aliases = [a for a in aliases if is_selected(a)]
    selected_macros = [m for m in macros if is_selected(m)]

    with open(header_file, 'w') as hf:
        hf.write("#ifndef EXTRACTED_CODE_H\n#define EXTRACTED_CODE_H\n\n")

        if selected_macros:
            hf.write("// Extracted Macros\n")
            for macro in selected_macros:
                macro_code = extract_code_from_node(macro)
                if macro_code:
                    if not macro_code.startswith("#"):
                        hf.write("#define " + macro_code + "\n")
                    else:
                        hf.write(macro_code + "\n")
            hf.write("\n")

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

        def add_to_grouped_items(item_node, code_snippet):
            path_nodes = get_full_namespace_and_class_path(item_node)
            path_key = tuple(n.get_usr() for n in path_nodes)
            if path_key not in grouped_items:
                grouped_items[path_key] = {'nodes': path_nodes, 'items': []}
            if code_snippet not in grouped_items[path_key]['items']:
                grouped_items[path_key]['items'].append(code_snippet)

        # Handle aliases
        for al in selected_aliases:
            al_code = extract_code_from_node(al)
            if al_code:
                add_to_grouped_items(al, al_code)

        # Handle enums
        for enm in selected_enums:
            enm_code = extract_code_from_node(enm)
            if enm_code:
                add_to_grouped_items(enm, enm_code)

        # Handle classes/structs/unions/templates
        for cls in selected_classes:
            class_code = extract_code_from_node(cls)
            if class_code:
                add_to_grouped_items(cls, class_code)


        for func in selected_functions:
            if func.kind == clang.cindex.CursorKind.CXX_METHOD:
                curr, skip = func.semantic_parent, False
                while curr and curr.kind != clang.cindex.CursorKind.TRANSLATION_UNIT:
                    if is_selected(curr):
                        skip = True
                        break
                    curr = curr.semantic_parent
                if skip:
                    continue

            signature = format_function_signature(func)
            if signature:
                if func.kind == clang.cindex.CursorKind.CXX_METHOD:
                    if "::" + func.spelling in signature:
                         idx = signature.rfind("::" + func.spelling)
                         start_idx = idx
                         while start_idx > 0 and (signature[start_idx-1].isalnum() or signature[start_idx-1] in '_:'):
                             start_idx -= 1
                         signature = signature[:start_idx] + signature[idx+2:]
                add_to_grouped_items(func, signature)

        for var in selected_variables:
            curr, already_in_header = var.semantic_parent, False
            while curr and curr.kind != clang.cindex.CursorKind.TRANSLATION_UNIT:
                if is_selected(curr):
                    already_in_header = True
                    break
                curr = curr.semantic_parent
            if already_in_header:
                continue

            item = f"{var.type.spelling} {var.spelling};"
            if var.semantic_parent.kind in [clang.cindex.CursorKind.CLASS_DECL,
                                            clang.cindex.CursorKind.STRUCT_DECL,
                                            clang.cindex.CursorKind.UNION_DECL,
                                            clang.cindex.CursorKind.CLASS_TEMPLATE]:
                item = f"static {item}"
            else:
                item = f"extern {item}"
            add_to_grouped_items(var, item)

        def emit_grouped_items(items_dict):
            tree = {}
            for path_key, data in items_dict.items():
                items, path_nodes = data['items'], data['nodes']
                curr_level = tree
                for node in path_nodes:
                    key = (node.kind, node.spelling, node.get_usr())
                    if key not in curr_level:
                        curr_level[key] = {'_node': node, '_items': [], '_children': {}}
                    if node == path_nodes[-1]:
                        curr_level[key]['_items'].extend(items)
                    curr_level = curr_level[key]['_children']
                if not path_nodes:
                    if None not in tree:
                        tree[None] = {'_items': [], '_children': {}}
                    tree[None]['_items'].extend(items)

            def get_template_params(node):
                params = []
                for child in node.get_children():
                    if child.kind == clang.cindex.CursorKind.TEMPLATE_TYPE_PARAMETER:
                        params.append(f"typename {child.spelling}")
                    elif child.kind == clang.cindex.CursorKind.TEMPLATE_NON_TYPE_PARAMETER:
                        params.append(f"{child.type.spelling} {child.spelling}")
                return ", ".join(params)

            def print_tree(node_tree, indent=""):
                res = []
                if None in node_tree:
                    for item in node_tree[None]['_items']:
                        res.append(f"{indent}{item}")
                    del node_tree[None]

                for key, data in sorted(node_tree.items(), key=lambda x: x[0][1] if x[0] else ""):
                    kind, name, usr = key
                    node = data['_node']
                    if kind == clang.cindex.CursorKind.NAMESPACE:
                        res.append(f"{indent}namespace {name} {{")
                        res.extend(print_tree(data['_children'], indent + "    "))
                        for item in data['_items']:
                            res.append(f"{indent}    {item}")
                        res.append(f"{indent}}} // namespace {name}")
                    else:
                        # If it's a class/struct/union/template that is ALREADY extracted as a block
                        # we don't want to re-wrap it in a minimal declaration.
                        # We check if any item is the full definition.
                        is_full_definition = False
                        for item in data['_items']:
                            if name in item and ('{' in item or 'enum' in item or 'union' in item):
                                is_full_definition = True
                                break

                        if is_full_definition:
                             for item in data['_items']:
                                 res.append(f"{indent}{item}")
                             res.extend(print_tree(data['_children'], indent))
                        else:
                            k = "class"
                            if kind == clang.cindex.CursorKind.STRUCT_DECL:
                                k = "struct"
                            elif kind == clang.cindex.CursorKind.UNION_DECL:
                                k = "union"
                            elif kind == clang.cindex.CursorKind.CLASS_TEMPLATE:
                                params = get_template_params(node)
                                res.append(f"{indent}template <{params}>")
                                k = "struct" if "struct" in extract_code_from_node(node).split('{')[0] else "class"
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
        hf.write("\n#endif // EXTRACTED_CODE_H\n")

    with open(cpp_file, 'w') as cf:
        cf.write('#include "extracted_code.h"\n\n')
        cf.write("// Implementations of extracted functions\n")
        for func in selected_functions:
            if func.kind == clang.cindex.CursorKind.CXX_METHOD:
                curr, skip = func.semantic_parent, False
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
                    full_class_path = []
                    curr_p = func.semantic_parent
                    while curr_p and curr_p.kind in [clang.cindex.CursorKind.CLASS_DECL,
                                                     clang.cindex.CursorKind.STRUCT_DECL,
                                                     clang.cindex.CursorKind.UNION_DECL,
                                                     clang.cindex.CursorKind.CLASS_TEMPLATE]:
                        full_class_path.append(curr_p.spelling)
                        curr_p = curr_p.semantic_parent
                    full_class_qualifier = "::".join(reversed(full_class_path))

                    if '{' in func_code:
                        # Split by the FIRST '{' but we must be careful with initializers.
                        # Actually, methods MUST have a body starting with { in our case (is_definition)
                        # but that { might be far.
                        idx = func_code.find('{')
                        prefix, body = func_code[:idx], func_code[idx:]
                        if func.spelling in prefix:
                             last_idx = prefix.rfind(func.spelling)
                             new_prefix = prefix[:last_idx] + full_class_qualifier + "::" + prefix[last_idx:]
                             func_code = new_prefix + body

                if ns:
                    already_wrapped = False
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
    input_path, output_path = Path(input_file), Path(output_dir)
    if not input_path.exists():
        logging.error(f"Input file {input_path} does not exist.")
        return [], [], [], [], [], []
    output_path.mkdir(exist_ok=True)
    functions, variables, classes, includes, enums, aliases, macros = parse_clang_ast(input_path)
    if target_names is None:
        logging.info("Found functions: " + ", ".join([f.spelling for f in functions]))
        logging.info("Found variables: " + ", ".join([v.spelling for v in variables]))
        logging.info("Found classes: " + ", ".join([c.spelling for c in classes]))
        logging.info("Found enums: " + ", ".join([e.spelling for e in enums]))
        logging.info("Found aliases: " + ", ".join([a.spelling for a in aliases]))
        logging.info("Found macros: " + ", ".join([m.spelling for m in macros]))
        return functions, variables, classes, enums, aliases, macros
    generate_cpp_header_and_implementation(output_path, functions, variables, classes, includes, enums, aliases, macros, target_names)
    return functions, variables, classes, enums, aliases, macros

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="C++ Code Extraction and Refactoring Tool")
    parser.add_argument("input_file", type=str, help="Path to the input C++ source file.")
    parser.add_argument("-o", "--output_dir", type=str, default="./output", help="Output directory.")
    parser.add_argument("-t", "--targets", type=str, nargs='*', help="Specific names to extract.")
    args = parser.parse_args()
    main(args.input_file, args.output_dir, args.targets)
