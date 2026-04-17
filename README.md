# leetcode-cli

A feature-rich command-line interface for LeetCode. Browse, edit, test, and submit LeetCode problems directly from your terminal.

## Features

- **Authentication**: Automatically extracts LeetCode session cookies from your local browser (`lc auth`).
- **Problem Browsing**: List problems with filters for tags, difficulty, and search keywords (`lc list`, `lc tags`).
- **Problem Details**: View problem descriptions directly in the terminal, rendered in markdown (`lc pick`).
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
