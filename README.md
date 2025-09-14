# Lean `sorry` Issue Creator

This script automates the process of finding `sorry` proof obligations in a Lean project, analyzing them with the Gemini AI model, and creating detailed, context-rich GitHub issues.

It is designed to streamline the management of formal verification projects by converting unfinished proofs into actionable, well-documented tasks.

:warning: Whilst this has worked locally for some projects, it has not been extensively tested, may still have rough edges and incomplete implementations of features.

## How it Works

The script scans a Lean project for `sorry` statements. For each one it finds, it gathers a comprehensive context package, including:
1.  The content of the file with the `sorry`.
2.  The content of all imported files found locally (including from dependencies like `Mathlib`).
3.  The content of any academic papers or websites provided as URL references.
4.  (Optional) The results of a web search for any definitions it cannot find locally.

This rich context is then sent to the Gemini API, which generates a detailed analysis and proof suggestion. Finally, the script creates a GitHub issue, complete with the AI's analysis, code snippets, and a link to the relevant line of code.

## Features

-   **Automated `sorry` Detection**: Recursively scans a Lean project to find all instances of `sorry`.
-   **AI-Powered Analysis**: Leverages the Gemini API to provide high-quality analysis for each proof obligation.
-   **Rich Context Generation**:
    -   **Local Dependencies**: Automatically finds and reads imported files from the project and its `.lake` dependencies (e.g., `Mathlib`).
    -   **External References**: Ingests content from user-provided URLs (PDFs or websites) to use as context.
    -   **Web Search Fallback**: Can optionally perform a web search to find definitions not available locally.
-   **Efficient and Safe**:
    -   **Concurrent Processing**: Uses multiple threads to process `sorry`s in parallel, dramatically speeding up execution.
    -   **Duplicate Prevention**: Intelligently checks for existing GitHub issues to avoid creating duplicates.
    -   **Dry Run Mode**: A `--dry-run` flag allows you to preview the script's actions without making any API calls or creating issues.
-   **Highly Configurable**:
    -   Customize the issue labels, Gemini model, and target repository via command-line flags.

## Prerequisites

1.  **Python 3.7+** and the `pip` package manager.
2.  **GitHub CLI**: The `gh` command-line tool must be installed and authenticated. Run `gh auth login` to set it up.
3.  **Google Cloud SDK**: The `gcloud` CLI is required for authenticating with the Gemini API. Run `gcloud auth application-default login` to set up Application Default Credentials.
4.  **Project Dependencies**: Before running the script on a Lean project, ensure you have downloaded its dependencies by running `lake build` within the project's root directory.

## Installation

The script requires the `google-generativeai` library. It is recommended to install it in a virtual environment.

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the required package
pip install google-generativeai
```

## Usage

The script is run from the command line, pointing it to the target repository you wish to analyze.

```bash
python3 issues.py --repo-path /path/to/your/lean/project [OPTIONS] [SEARCH_PATH]
```

### Arguments

-   `--repo-path` (required): The absolute or relative path to the root of the target git repository.
-   `search_path` (optional): The sub-directory within the repository to scan. Defaults to the entire repository.

### Options

-   `--dry-run`: Simulate the script's execution. It will print the `sorry`s it finds but will not call the Gemini API or create GitHub issues.
-   `--label LABEL`: The GitHub issue label to use. (Default: `proof wanted`).
-   `--model MODEL`: The Gemini model to use for analysis. (Default: `gemini-2.5-pro`).
-   `--reference-url URL`: A URL to a PDF or webpage to be used as context. This flag can be specified multiple times.
-   `--web-search`: If an imported definition cannot be found locally, perform a web search as a fallback.

### Example Workflow

Imagine you are working on a project at `~/my-lean-project` and want to create issues for all `sorry`s in the `src/theorems` directory. You have a relevant research paper that provides context.

First, ensure the project's dependencies are downloaded:
```bash
cd ~/my-lean-project
lake build
cd -
```

Now, run the script:

```bash
python3 issues.py \
  --repo-path ~/my-lean-project \
  --reference-url https://arxiv.org/pdf/1234.56789.pdf \
  --web-search \
  src/theorems
```

The script will then:
-   Scan for `.lean` files only within the `src/theorems` sub-directory.
-   Fetch the content from the provided arXiv URL.
-   For each `sorry` found, it will:
    -   Find and read all locally imported files (from your project and its dependencies).
    -   Perform a web search for any imports it cannot find locally.
    -   Call the Gemini API with the combined context.
    -   Check if a similar issue already exists.
    -   Create a new GitHub issue with the AI-generated analysis.
