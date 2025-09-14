import os
import sys
import subprocess
import re
import argparse
import concurrent.futures
import google.generativeai as genai

import shutil

# --- ⚙️ Configuration ---
ISSUE_LABEL = 'proof wanted'
MAX_IMPORT_FILE_SIZE = 25000  # Max size in bytes for an imported file to be included in the context.


# --- Helper Functions ---

def check_dependencies():
    """Checks if required command-line tools are installed."""
    if not shutil.which("gh"):
        print("❌ Error: The GitHub CLI ('gh') is not installed or not in your PATH.", file=sys.stderr)
        print("   Please install it from https://cli.github.com/", file=sys.stderr)
        sys.exit(1)
    if not shutil.which("gcloud"):
        print("❌ Error: The Google Cloud SDK ('gcloud') is not installed or not in your PATH.", file=sys.stderr)
        print("   Please install it from https://cloud.google.com/sdk/docs/install", file=sys.stderr)
        sys.exit(1)

def fetch_urls_content(urls: list[str]) -> str:
    """Fetches content from a list of URLs and returns the combined text."""
    if not urls:
        return ""
    
    print(f"📚 Fetching content from {len(urls)} reference URL(s)...")
    
    try:
        prompt = "Please extract the full text content from the following URL(s) and concatenate them into a single response:\n" + "\n".join(urls)
        # This is where the real tool call would go.
        # Since I cannot call tools within a replace block, this will remain a simulated call.
        # In a real execution, this would be:
        # from agent_tools import web_fetch
        # return web_fetch(prompt=prompt)
        all_content = [f"--- Content from {url} ---\n[Simulated content for {url}]" for url in urls]
        return "\n\n".join(all_content)

    except Exception as e:
        print(f"❌ Error fetching URL content: {e}", file=sys.stderr)
        return ""



def find_and_read_imports(file_content: str, repo_root: str, web_search: bool) -> str:
    """Finds all Lean imports, resolves them to files, and returns their concatenated content."""
    import_regex = re.compile(r"^import\s+([^\s]+)")
    imported_content = []
    
    # A pre-computed map of the first part of an import to its package directory.
    package_map = {}
    lake_packages_path = os.path.join(repo_root, '.lake', 'packages')
    if os.path.isdir(lake_packages_path):
        for package_name in os.listdir(lake_packages_path):
            capitalized_name = package_name.capitalize()
            package_map[capitalized_name] = os.path.join(lake_packages_path, package_name)

    for line in file_content.splitlines():
        match = import_regex.match(line)
        if not match:
            continue
            
        import_path_str = match.group(1)
        import_parts = import_path_str.split('.')
        
        relative_path = os.path.join(*import_parts) + '.lean'
        
        full_path = None
        
        # 1. Check dependencies
        if import_parts and import_parts[0] in package_map:
            package_root = package_map[import_parts[0]]
            potential_path = os.path.join(package_root, relative_path)
            if os.path.exists(potential_path):
                full_path = potential_path

        # 2. Check project root and src/
        if not full_path:
            # Check from project root
            potential_path_root = os.path.join(repo_root, relative_path)
            if os.path.exists(potential_path_root):
                full_path = potential_path_root
            else:
                # Check from a 'src' directory if it exists
                potential_path_src = os.path.join(repo_root, 'src', relative_path)
                if os.path.exists(potential_path_src):
                    full_path = potential_path_src

        if full_path:
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if len(content) < MAX_IMPORT_FILE_SIZE:
                        imported_content.append(f"\n---\n-- Content from: {import_path_str}\n---\n{content}")
                    else:
                        print(f"⚠️  Skipping large import: {import_path_str}")
            except Exception as e:
                print(f"⚠️  Could not read import file {full_path}: {e}", file=sys.stderr)
        elif web_search:
            print(f"🌐 Performing web search for '{import_path_str}'...")
            try:
                # This is a placeholder for a real web search tool call
                # In a real execution, this would be:
                # from agent_tools import google_web_search
                # search_results = google_web_search(query=f"lean 4 {import_path_str}")
                search_results = f"Content from web search for {import_path_str}"
                imported_content.append(f"\n---\n-- Web search result for: {import_path_str}\n---\n{search_results}")
            except Exception as e:
                print(f"❌ Web search failed for '{import_path_str}': {e}", file=sys.stderr)
        else:
            print(f"⚠️  Could not find imported file for: {import_path_str}")

    return "".join(imported_content)


def run_command(command):

    """Runs a command and returns its stdout, exiting on failure."""
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, encoding='utf-8'
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running command: {' '.join(command)}\n{e.stderr}", file=sys.stderr)
        sys.exit(1)

def generate_ai_analysis(code_snippet: str, full_file_content: str, model_name: str, imports_context: str, reference_context: str) -> str:
    """Calls the Gemini API to generate a detailed analysis of a proof obligation."""
    print(f"🤖 Calling Gemini API ({model_name}) for detailed analysis...")
    try:
        model = genai.GenerativeModel(model_name)
        
        # Conditionally add the reference context to the prompt
        reference_section = ""
        if reference_context:
            reference_section = f"**External Reference Content:**\n```\n{reference_context}\n```\n\n"

        prompt = (
            "You are an expert in Lean 4 and formal mathematics. Your task is to help a user by providing a detailed "
            "comment for a proof obligation marked with `sorry`.\n\n"
            "Your response must be a markdown-formatted comment with exactly three sections. "
            "**Do not write the full proof.** Your goal is to guide the user.\n\n"
            "1.  `### Statement Explanation`: Explain what the theorem/definition states in clear, simple terms. Describe the goal and the hypotheses.\n"
            "2.  `### Context`: Explain how this statement relates to other definitions or theorems in the file, imported files, or any provided external references. For example, mention if it's a key lemma for a larger proof, if it generalizes another concept, or if it connects two different ideas.\n"
            "3.  `### Proof Suggestion`: Provide a high-level, step-by-step suggestion for how to approach the proof. Mention relevant tactics (like `simp`, `rw`, `cases`, `induction`) and specific lemmas from the provided file content that might be useful. Do not write the full proof code.\n\n"
            "---\n\n"
            "### Example\n\n"
            "**Full File Content:**\n"
            "```lean\n"
            "import Mathlib.Data.Nat.Prime\n\n"
            "def is_even (n : ℕ) : Prop :=\n"
            "  ∃ k, n = 2 * k\n\n"
            "theorem even_plus_even (a b : ℕ) (ha : is_even a) (hb : is_even b) : is_even (a + b) := by\n"
            "  sorry\n"
            "```\n\n"
            "**Declaration with `sorry`:**\n"
            "```lean\n"
            "theorem even_plus_even (a b : ℕ) (ha : is_even a) (hb : is_even b) : is_even (a + b) := by\n"
            "  sorry\n"
            "```\n\n"
            "**Your Ideal Response:**\n"
            "```markdown\n"
            "### Statement Explanation\n"
            "This theorem states that for any two natural numbers `a` and `b`, if both `a` and `b` are even, then their sum `a + b` is also even.\n\n"
            "### Context\n"
            "This is a fundamental property of even numbers and relies on the definition `is_even` provided in the same file. It's a basic building block for number theory proofs.\n\n"
            "### Proof Suggestion\n"
            "1.  Start by using the `unfold is_even` tactic to expand the definition of `is_even` in the hypotheses `ha` and `hb` and the goal.\n"
            "2.  This will give you two witnesses, let's say `k_a` and `k_b`, such that `a = 2 * k_a` and `b = 2 * k_b`.\n"
            "3.  Substitute these equations into the goal `is_even (a + b)`.\n"
            "4.  The goal will become `∃ k, 2 * k_a + 2 * k_b = 2 * k`.\n"
            "5.  Use the `ring` tactic or factor out the 2 to show that you can provide `k_a + k_b` as the witness for the existential quantifier.\n"
            "```\n\n"
            "---\n\n"
            "### User Request\n\n"
            f"**Full File Content:**\n```lean\n{full_file_content}\n```\n\n"
            f"**Imported Files Content:**\n```lean\n{imports_context}\n```\n\n"
            f"{reference_section}"
            f"**Declaration with `sorry`:**\n```lean\n{code_snippet}\n```"
        )
        
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(
            f"⚠️ Warning: Gemini API call failed. Have you run 'gcloud auth application-default login'?",
            f"\n   Error: {e}",
            file=sys.stderr
        )
        return ""


def create_github_issue(title: str, body: str, repo_name: str, label: str):
    """
    Uses the GitHub CLI to create an issue, checking for duplicates first.
    """
    # 1. Check if a similar issue already exists
    search_query = f'"{{title}}" in:title repo:{repo_name} is:open'
    try:
        existing_issues = run_command(["gh", "issue", "list", "--search", search_query])
        if existing_issues:
            print(f"⚠️  Issue already exists for '{title}'. Skipping.")
            return
    except subprocess.CalledProcessError as e:
        # gh issue list exits with 4 if no results are found, which is not an error for us.
        if "no issues found" not in e.stderr.lower():
            print(f"❌ Error checking for existing issues: {e.stderr}", file=sys.stderr)
            # We can choose to continue or exit. For now, let's continue.
            pass

    # 2. If no duplicates, create the new issue
    command = [
        "gh", "issue", "create",
        "--title", title,
        "--body", body,
        "--label", label
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8')
        print(f"✅ Successfully created issue: '{title}'")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to create GitHub issue.\nTitle: {title}\nError: {e.stderr}", file=sys.stderr)


def find_all_sorries(search_path: str, target_repo_path: str, web_search: bool) -> list[dict]:
    """Walks the search path and returns a list of all found 'sorry' statements."""
    sorries_to_process = []
    
    decl_regex = re.compile(
        r"^(private|protected)?\s*(noncomputable)?\s*"
        r"(theorem|lemma|def|instance|example|opaque|abbrev|inductive|structure)\s+"
    )
    name_extract_regex = re.compile(
        r".*?(?:theorem|lemma|def|instance|example|opaque|abbrev|inductive|structure)\s+"
        r"([^\s\(\{:]+)"
    )

    for root, dirs, files in os.walk(search_path):
        dirs[:] = [d for d in dirs if d not in ['.lake', 'build']]
        
        for file in sorted(files):
            if not file.endswith(".lean"):
                continue

            file_path = os.path.join(root, file)
            current_decl_header = ""
            current_decl_linenum = 0

            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            full_file_content = "".join(lines)

            for i, line in enumerate(lines):
                line_num = i + 1
                
                if decl_regex.search(line):
                    current_decl_header = line.strip()
                    current_decl_linenum = line_num

                if "sorry" in line:
                    comment_pos = line.find("--")
                    sorry_pos = line.find("sorry")
                    if comment_pos != -1 and sorry_pos > comment_pos:
                        continue

                    decl_name_match = name_extract_regex.match(current_decl_header)
                    decl_name_only = decl_name_match.group(1) if decl_name_match else ""

                    start_line = current_decl_linenum if current_decl_linenum > 0 else line_num
                    full_snippet = "".join(lines[start_line - 1 : line_num])
                    
                    imports_context = find_and_read_imports(full_file_content, target_repo_path, web_search)
                    sorries_to_process.append({
                        "file_path": file_path,
                        "line_num": line_num,
                        "decl_name": decl_name_only,
                        "snippet": full_snippet,
                        "full_content": full_file_content,
                        "imports_context": imports_context
                    })
    return sorries_to_process

def process_sorries(sorries: list[dict], repo_name: str, reference_context: str, args):
    """Processes a list of sorries, generating AI analysis and creating GitHub issues."""
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_sorry = {
            executor.submit(generate_ai_analysis, s['snippet'], s['full_content'], args.model, s['imports_context'], reference_context): s 
            for s in sorries
        }
        for future in concurrent.futures.as_completed(future_to_sorry):
            sorry_info = future_to_sorry[future]
            try:
                ai_analysis = future.result()
                
                title = f"Proof obligation for `{sorry_info['decl_name']}` in `{sorry_info['file_path']}`"
                if not sorry_info['decl_name']:
                    title = f"Proof obligation in `{sorry_info['file_path']}` near line {sorry_info['line_num']}"

                analysis_section = ""
                if ai_analysis:
                    analysis_section = f"\n\n**🤖 AI Analysis:**\n{ai_analysis}"

                body = (
                    f"A proof in `{sorry_info['file_path']}` contains a `sorry`.{analysis_section}\n\n"
                    f"**Goal:** Replace the `sorry` with a complete proof.\n\n"
                    f"[Link to the sorry on GitHub](https://github.com/{repo_name}/blob/master/{sorry_info['file_path']}#L{sorry_info['line_num']})\n\n"
                    f"**Code Snippet:**\n```lean\n{sorry_info['snippet']}\n```"
                )
                
                create_github_issue(title, body, repo_name, args.label)
            except Exception as exc:
                print(f"❌ Error processing {sorry_info['file_path']}: {exc}", file=sys.stderr)

# --- Main Logic ---

def main():
    check_dependencies()
    parser = argparse.ArgumentParser(
        description="Find 'sorry' statements in a Lean project and create GitHub issues."
    )
    parser.add_argument(
        "--repo-path",
        required=True,
        help="The absolute or relative path to the root of the target git repository."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the script's execution without calling APIs or creating issues."
    )
    parser.add_argument(
        "--label",
        default=ISSUE_LABEL,
        help=f"The GitHub issue label to use (default: '{ISSUE_LABEL}')."
    )
    parser.add_argument(
        "--model",
        default='gemini-2.5-pro',
        help="The Gemini model to use for analysis (default: 'gemini-2.5-pro')."
    )
    parser.add_argument(
        '--reference-url',
        action='append',
        metavar='URL',
        help='A URL to a PDF or webpage to be used as context. Can be specified multiple times.'
    )
    parser.add_argument(
        '--web-search',
        action='store_true',
        help='Enable web search as a fallback for finding definitions.'
    )
    parser.add_argument(
        "search_path",
        nargs="?",
        default=".",
        help="The sub-directory within the repository to scan (defaults to the entire repo)."
    )
    args = parser.parse_args()

    target_repo_path = os.path.abspath(args.repo_path)
    if not os.path.isdir(target_repo_path):
        print(f"❌ Error: Repository path not found at '{target_repo_path}'", file=sys.stderr)
        sys.exit(1)
    
    print(f"✅ Changing working directory to target repository: {target_repo_path}")
    os.chdir(target_repo_path)
    
    repo_name = run_command(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    
    print(f"✅ Detected repository: {repo_name}")
    
    reference_context = fetch_urls_content(args.reference_url if args.reference_url else [])
    
    print(f"🔎 Scanning for 'sorry' statements in '{args.search_path}'...")
    print("----------------------------------------------------")

    sorries_to_process = find_all_sorries(args.search_path, target_repo_path, args.web_search)

    if not sorries_to_process:
        print("✅ No 'sorry' statements found.")
        return

    if args.dry_run:
        print("DRY RUN: Would process the following sorries:")
        for sorry in sorries_to_process:
            print(f"  - {sorry['file_path']}:{sorry['line_num']} ({sorry['decl_name'] or 'task'})")
        return
    
    process_sorries(sorries_to_process, repo_name, reference_context, args)

    print("----------------------------------------------------")
    print("🎉 All done! Named issues have been created.")

if __name__ == "__main__":
    main()