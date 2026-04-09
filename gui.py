import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import subprocess
import os
from pathlib import Path
import refactor_tool
import logging
import traceback

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("C++ Function Extractor")
        self.root.geometry("800x600")

        self.input_file = tk.StringVar()
        self.output_dir = tk.StringVar(value="./output")
        self.found_items = []
        self.selected_items = {}

        self.setup_ui()
        self.setup_logging()

    def setup_ui(self):
        # File Selection
        file_frame = tk.LabelFrame(self.root, text="File Selection", padx=10, pady=10)
        file_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(file_frame, text="Input C++ File:").grid(row=0, column=0, sticky="w")
        tk.Entry(file_frame, textvariable=self.input_file, width=60).grid(row=0, column=1, padx=5)
        tk.Button(file_frame, text="Browse", command=self.browse_input).grid(row=0, column=2)

        tk.Label(file_frame, text="Output Directory:").grid(row=1, column=0, sticky="w")
        tk.Entry(file_frame, textvariable=self.output_dir, width=60).grid(row=1, column=1, padx=5)
        tk.Button(file_frame, text="Browse", command=self.browse_output).grid(row=1, column=2)

        tk.Button(file_frame, text="Analyze Code", command=self.analyze_code).grid(row=2, column=1, pady=5)

        # Selection Frame
        self.selection_frame = tk.LabelFrame(self.root, text="Select Items to Extract", padx=10, pady=10)
        self.selection_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.canvas = tk.Canvas(self.selection_frame)
        self.scrollbar = ttk.Scrollbar(self.selection_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            )
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        # Action Buttons
        btn_frame = tk.Frame(self.root, pady=10)
        btn_frame.pack(fill="x")
        tk.Button(btn_frame, text="Extract Selected", command=self.extract_code).pack(side="left", padx=20)
        tk.Button(btn_frame, text="Compile Extracted", command=self.compile_code).pack(side="left", padx=20)

        # Log Frame
        log_frame = tk.LabelFrame(self.root, text="Logs", padx=10, pady=10)
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.log_text = tk.Text(log_frame, height=10)
        self.log_text.pack(fill="both", expand=True)

    def setup_logging(self):
        class TextHandler(logging.Handler):
            def __init__(self, text_widget):
                super().__init__()
                self.text_widget = text_widget
            def emit(self, record):
                msg = self.format(record)
                self.text_widget.after(0, self.append_log, msg)
            def append_log(self, msg):
                self.text_widget.insert(tk.END, msg + "\n")
                self.text_widget.see(tk.END)

        handler = TextHandler(self.log_text)
        handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

    def browse_input(self):
        path = filedialog.askopenfilename(filetypes=[("C++ files", "*.cpp *.cxx *.cc *.h *.hpp")])
        if path:
            self.input_file.set(path)

    def browse_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_dir.set(path)

    def analyze_code(self):
        if not self.input_file.get():
            messagebox.showwarning("Warning", "Please select an input file.")
            return

        def task():
            try:
                # We need to run this in a thread because it might take time
                result = refactor_tool.main(self.input_file.get(), self.output_dir.get())

                # result is now (functions, variables, classes, enums, aliases, macros)
                self.found_items = []
                for items in result:
                    if isinstance(items, list):
                        self.found_items.extend(items)

                self.root.after(0, self.update_selection_list)
            except Exception as e:
                logging.error(f"Analysis failed: {e}")
                logging.error(traceback.format_exc())

        threading.Thread(target=task).start()

    def update_selection_list(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        self.selected_items = {}
        for item in self.found_items:
            var = tk.BooleanVar()
            # Use USR as key to handle overloads
            self.selected_items[item.get_usr()] = var

            # Map Clang kind to readable text
            kind_map = {
                "FUNCTION_DECL": "Function",
                "CXX_METHOD": "Method",
                "VAR_DECL": "Variable",
                "CLASS_DECL": "Class",
                "STRUCT_DECL": "Struct",
                "UNION_DECL": "Union",
                "CLASS_TEMPLATE": "Class Template",
                "FUNCTION_TEMPLATE": "Function Template",
                "ENUM_DECL": "Enum",
                "TYPEDEF_DECL": "Typedef",
                "TYPE_ALIAS_DECL": "Using Alias",
                "MACRO_DEFINITION": "Macro"
            }
            kind = kind_map.get(item.kind.name, item.kind.name.split('_')[-1].title())

            # Get full name to help disambiguate items in different namespaces/classes
            display_name = refactor_tool.get_full_name(item) if item.kind.name != "MACRO_DEFINITION" else item.spelling
            loc = f"{item.location.line}:{item.location.column}"
            cb = tk.Checkbutton(self.scrollable_frame, text=f"{kind}: {display_name} ({loc})", variable=var)
            cb.pack(anchor="w")

    def extract_code(self):
        targets = [usr for usr, var in self.selected_items.items() if var.get()]
        if not targets:
            messagebox.showwarning("Warning", "No items selected.")
            return

        def task():
            try:
                refactor_tool.main(self.input_file.get(), self.output_dir.get(), targets)
                logging.info("Extraction complete.")
            except Exception as e:
                logging.error(f"Extraction failed: {e}")

        threading.Thread(target=task).start()

    def compile_code(self):
        output_dir = Path(self.output_dir.get())
        cpp_file = output_dir / 'extracted_code.cpp'
        if not cpp_file.exists():
            messagebox.showerror("Error", "Extracted CPP file not found.")
            return

        def task():
            try:
                logging.info(f"Compiling {cpp_file}...")
                cmd = ['g++', '-c', str(cpp_file), '-o', str(cpp_file.with_suffix('.o'))]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    logging.info("Compilation successful.")
                else:
                    logging.error(f"Compilation failed:\n{result.stderr}")
            except Exception as e:
                logging.error(f"Compilation error: {e}")

        threading.Thread(target=task).start()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
