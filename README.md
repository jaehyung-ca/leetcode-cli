# leetcode-cli

A feature-rich command-line interface for LeetCode. Browse, edit, test, and submit LeetCode problems directly from your terminal.

## Features

- **Authentication**: Automatically extracts LeetCode session cookies from your local browser (`lc auth`).
- **Problem Browsing**: List problems with filters for tags, difficulty, and search keywords (`lc list`, `lc tags`).
- **Problem Details**: View problem descriptions directly in the terminal, rendered in markdown (`lc pick`).
- **Skill Sets**: Curated problem sets per topic (arrays, DP, graphs, ...) plus Blind 75 and a big-tech frequency list, with your solved progress and a level per set (`lc sets`, `lc set`).
- **Code Editor**: Generate starter code and open it in your `$EDITOR` (`lc edit`).
- **Testing & Submission**: Run example test cases (`lc test`) and submit solutions to LeetCode (`lc exec`).

## Requirements

- Python >= 3.8

## Installation

You can install `leetcode-cli` locally using `pip`:

```bash
git clone https://github.com/yourusername/leetcode-cli.git
cd leetcode-cli
pip install .
```

After installation, the `lc` command will be available in your terminal.

## Usage

### Commands Quick Reference

| Command | Alias | Description | Example |
|---------|-------|-------------|---------|
| `lc auth` | `lc a` | Extract browser cookies for authentication | `lc a` |
| `lc list` | `lc l` | List problems with optional filters | `lc l --diff easy --limit 10` |
| `lc tags` | `lc tg` | List available problem tags | `lc tg` |
| `lc pick` | `lc p` | View specific problem details and description | `lc p two-sum` |
| `lc random` | `lc r` | View a randomly selected problem | `lc r -d hard` |
| `lc sets` | `lc ss` | List curated skill sets with your progress | `lc ss` |
| `lc set` | `lc s` | Show one set's problems, or pick the next unsolved one | `lc s dp-1d -u`, `lc s graphs -r` |
| `lc edit` | `lc e` | Generate starter code and open in editor | `lc e 1` |
| `lc test` | `lc t` | Run example test cases on local file | `lc t 1` |
| `lc exec` | `lc x` | Submit a local file's solution to LeetCode | `lc x 1.two-sum.py` |

### Authentication
Start by extracting your browser cookies to authenticate with LeetCode. Ensure you are logged into LeetCode on Chrome/Firefox/Edge/Safari.
```bash
lc auth
# Alias: lc a
```

### Browse Problems
List recent problems or filter by difficulty/tags/sorting:
```bash
lc list
lc list --diff easy --limit 10
lc list --tag array --search "sum"
lc list --high ac
lc list --low freq --limit 20
# Alias: lc l
```

View available tags:
```bash
lc tags
# Alias: lc tg
```

### View Problem
Given a problem ID or a slug (e.g., `two-sum` or `1`), view the description:
```bash
lc pick 1
# Alias: lc p 1
```

You can also fetch and view a random problem, optionally filtered by tag or difficulty:
```bash
lc random
lc random --diff hard --tag array
# Alias: lc r
```

### Skill Sets
Curated problem sets, one per skill/topic, built from the canonical problems for each technique, the Blind 75 list, and problems that show up most often in big-tech interviews. Use them to check where you stand and what to practice next.

```bash
lc sets                     # every set with solved/total, per-difficulty counts, and a level
lc set dp-1d                # problems in a set, with your ✔/✘ status
lc set trees -u             # only the ones you have not solved
lc set graphs -d hard       # filter by difficulty
lc set blind75 -r           # open a random unsolved problem from the set
lc set heap -n              # open the first unsolved problem (sets are ordered easy → hard)
lc sets --offline           # skip the LeetCode request (no status)
# Aliases: lc ss, lc s
```

Set names accept unique prefixes (`lc s back` → `backtracking`). Progress is weighted (Easy 1, Medium 2, Hard 3) and mapped to a level: Untouched, Started, Learning, Solid, Strong, Mastered.

Built-in sets: `arrays-hashing`, `two-pointers`, `sliding-window`, `prefix-sum`, `binary-search`, `stack`, `linked-list`, `trees`, `trie`, `heap`, `backtracking`, `graphs`, `graphs-advanced`, `union-find`, `dp-1d`, `dp-2d`, `greedy`, `intervals`, `strings`, `math-geometry`, `bit-manipulation`, `design`, `advanced-structures`, `blind75`, `big-tech`.

A few canonical premium-only problems (e.g. Meeting Rooms II) are included and marked with 🔒.

#### Custom sets
Add your own sets in `~/.config/leetcode-cli/sets.json`. Entries can be slugs or problem IDs; a set with the same name as a built-in one replaces it.

```json
{
    "my-weak-spots": {
        "description": "Things I keep failing",
        "problems": ["two-sum", 146, "merge-k-sorted-lists"]
    }
}
```

### Edit Solution
Generates starter Python boilerplate and opens your default editor (e.g., `vi`). The file will be cached in your local `~/leetcode` directory by default.
```bash
lc edit two-sum
# Alias: lc e two-sum
```

### Test and Submit
Run LeetCode public example test cases on your local file:
```bash
lc test 1
# Or provide the path directly: lc test ~/leetcode/1.two-sum.py
# Alias: lc t 1
```

Submit your solution:
```bash
lc exec 1
# Alias: lc x 1
```

## Configuration

You can customize `leetcode-cli` by editing the configuration file located at `~/.config/leetcode-cli/config.json`.

### Set Cache Directory
By default, the `lc edit` command saves problem templates to `~/leetcode`. You can change this directory by setting `cache_dir` in `config.json`:

```json
{
    "cache_dir": "/path/to/your/custom/directory"
}
```

## Dependencies
- `typer`: For CLI parsing
- `rich`: For beautiful terminal output
- `requests` & `curl-cffi`: For HTTP requests and avoiding Cloudflare checks
- `browser-cookie3`: For seamless browser authentication 
- `beautifulsoup4` & `markdownify`: For HTML parsing and markdown rendering

## License
MIT License
