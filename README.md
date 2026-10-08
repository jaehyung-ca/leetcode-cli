# leetcode-cli

A feature-rich command-line interface for LeetCode. Browse, edit, test, and submit LeetCode problems directly from your terminal, either through subcommands or a tmux-based TUI (`lc`).

## Features

- **TUI**: `lc` with no arguments opens a two-pane tmux workspace: a vi-style browser (curated sets, topics, search, recent, passed, failed) with the problem description on the left, your editor (nvim) on the right; the recently-opened list syncs across machines via Google Drive or git (`lc tui`).
- **Authentication**: Automatic: every run picks up your LeetCode session cookies from Brave or Firefox, and re-reads them if LeetCode rejects the stored ones. When the login has expired, `lc` opens the LeetCode login page in your browser and carries on once you have logged in (`lc auth` still forces a refresh).
- **Problem Browsing**: List problems with filters for tags, difficulty, and search keywords (`lc list`, `lc tags`).
- **Problem Details**: View problem descriptions directly in the terminal, rendered in markdown (`lc pick`).
- **Your Lists**: Save the problem you are working on to your own lists, each with a note, from the TUI (`a`) or the CLI (`lc save`, `lc lists`); lists sync across machines with the recent list.
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
| `lc` / `lc tui` | `lc ui` | Open the TUI | `lc` |
| `lc auth` | `lc a` | Extract browser cookies for authentication | `lc a` |
| `lc list` | `lc l` | List problems with optional filters | `lc l --diff easy --limit 10` |
| `lc tags` | `lc tg` | List available problem tags | `lc tg` |
| `lc pick` | `lc p` | View specific problem details and description | `lc p two-sum` |
| `lc random` | `lc r` | View a randomly selected problem | `lc r -d hard` |
| `lc sets` | `lc ss` | List curated skill sets with your progress | `lc ss` |
| `lc set` | `lc s` | Show one set's problems, or pick the next unsolved one | `lc s dp-1d -u`, `lc s graphs -r` |
| `lc save` | `lc sv` | Save a problem to one of your lists with a note | `lc save redo 146 -m "use OrderedDict"` |
| `lc unsave` | | Remove a problem from a list | `lc unsave redo 146` |
| `lc lists` | `lc ls` | Show your lists, or one list with its notes | `lc ls redo` |
| `lc edit` | `lc e` | Generate starter code and open in editor | `lc e 1` |
| `lc test` | `lc t` | Run example test cases on local file | `lc t 1` |
| `lc exec` | `lc x` | Submit a local file's solution to LeetCode | `lc x 1.two-sum.py` |

### TUI
Run `lc` (or `lc tui`) to get a two-pane workspace. With tmux installed it opens a new tmux window (or starts a tmux session if you are not in one):

```
+---------------------------+---------------------------+
| main                      | edit                      |
|  menu > list > problem    |  $EDITOR (nvim)           |
|  (hjkl navigation)        |                           |
|---------------------------|                           |
|  test / submit / sync log |                           |
+---------------------------+---------------------------+
```

The **main** pane starts on a menu; pick a section by its first letter:

| Key | Section | Contents |
|-----|---------|----------|
| `c` | curated | Skill sets with your progress (see below) |
| `t` | topics  | Every LeetCode tag with solved/total, then the problems in a tag |
| `s` | search  | All problems, with the filter box focused |
| `r` | recent  | Problems you opened most recently |
| `p` | passed  | Accepted problems |
| `f` | failed  | Attempted but not accepted |
| `m` | my lists | Your lists of saved problems, with notes |

Navigation is vi-style: `j`/`k` move, `l` (or `Enter`) opens, `h` goes back. Problem lists show LeetCode's interview-frequency score in the last column; `f` sorts by it. Opening a problem shows its description in the main pane, writes the starter file if it does not exist yet, and loads it in the **edit** pane; `l` on the problem view jumps to the editor. `t` and `x` save the editor, run the tests or submit, and print the result in the log at the bottom. An accepted submission marks the problem ✔ immediately.

| Key | Action |
|-----|--------|
| `j` / `k` | Move in a list, or scroll the problem description |
| `l` / `Enter` | Open the highlighted section, set, tag or problem; on a problem, jump to the editor |
| `h` / `Backspace` | Back |
| `Esc` | Leave the filter box |
| `/` | Filter the current list (ID, title, slug, set or tag name) |
| `u` / `d` / `f` | Toggle unsolved-only / cycle difficulty (all → easy → medium → hard) / sort by interview frequency |
| `r` / `n` | Open a random / the first unsolved problem in the current list (or the highlighted set) |
| `t` / `x` | Test / submit the open problem |
| `e` / `o` | Re-open the solution in the editor / open the problem on leetcode.com |
| `a` | Save the problem (on screen, highlighted, or open) to a list with a note |
| `D` | In a list: remove the highlighted problem from it |
| `Ctrl-d` / `Ctrl-u`, `g` / `G` | Page / jump in the problem description |
| `Ctrl-r` / `Ctrl-l` | Refresh the problem list / clear the log |
| `?` | Help |
| `q` | Save, sync the recent list and lists (Drive or git), quit |

The edit pane runs `$EDITOR` in the scratch work directory. With `nvim` it is started with `--listen`, so the main pane switches files over RPC (and saves before test/submit). `vi`/`vim` are driven with tmux `send-keys`; any other editor is restarted with the new file.

Without tmux, `lc` runs as a single window and `e` suspends the TUI to run `$EDITOR`.

#### Solution files, state and sync
Solution files are scratch files. They are written to `$TMPDIR/lc-work/` (`/tmp/lc-work/`), recreated from the LeetCode snippet when missing, kept until the machine reboots, and never recorded anywhere else. Override the directory with `LC_WORK_DIR`.

The tool's own state lives in the data directory (default `~/.config/leetcode-cli`, see Configuration): the recently opened problems (`recent.json`), your lists with notes (`lists.json`) and the full problem list with your solved status (`index.json`, refreshed in the background so the TUI starts instantly).

Only the recent list and your lists need to follow you across machines (solved/failed status comes from your LeetCode account), and they can be synced two ways:

- **Google Drive** (via [rclone](https://rclone.org)): set `drive_folder_id` to the ID from the folder's share link and `rclone_remote` to an rclone remote of type `drive` that can write to that folder (`rclone config` to create one). On startup the remote files are merged into the local ones (union by problem, latest timestamp wins; removing a problem from a list leaves a marker so the removal syncs too); on `q` the merged files are uploaded.
- **git**: if no Drive folder is configured and the data directory is a git repository (`index.json` is git-ignored automatically), `q` commits its changes (`lc sync <date> (<n> files)`) and pushes when a remote is configured.

On the first `lc` launch with no sync configured (and rclone with a `drive` remote available), the tool asks whether to set up Drive sync: pick the remote, then paste the folder's share link or ID, or press Enter to use (or create) a `leetcode-cli` folder at the remote's root. The settings are written to `config.json`; answering no stores `"data_sync": "none"`. Run `lc sync-setup` to configure it again.

`data_sync` in the config forces a mode (`"drive"`, `"git"` or `"none"`). If a sync fails the error is shown in the log and `q` again quits anyway.

### Authentication
Nothing to set up: log in to leetcode.com in Brave or Firefox and `lc` reads the session cookies from there on each run. If LeetCode rejects them, `lc` looks in the browser again; if the login has expired, it opens the login page in your browser, waits (up to 5 minutes) for you to log in, and continues. To pull the cookies by hand:
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

### Your Lists
Keep your own lists of problems (to redo, tricky ones, interview prep, ...), each problem with a note. In the TUI press `a` on a problem: type a list name (existing names complete with `→`; a new name creates the list), Enter, then the note, Enter. Saving a problem that is already in the list updates its note. The lists show under `m` (my lists); the problem view shows which lists the problem is in and their notes.

From the command line the problem defaults to the one you opened last:

```bash
lc save redo -m "use OrderedDict + move_to_end"   # last opened problem
lc save tricky 42 -m "two pointers, not a stack"
lc lists                                           # every list with counts
lc lists redo                                      # problems and notes (prefix is fine)
lc unsave redo 146
# Aliases: lc sv, lc ls
```

### Edit Solution
Generates starter Python boilerplate and opens your default editor (e.g., `vi`). The file is a scratch file in `/tmp/lc-work/` (kept until reboot), so `lc test 1` / `lc exec 1` find it by ID afterwards.
```bash
lc edit two-sum
# Alias: lc e two-sum
```

### Test and Submit
Run LeetCode public example test cases on your local file:
```bash
lc test 1
# Or provide the path directly: lc test /tmp/lc-work/1.two-sum.py
# Alias: lc t 1
```

Submit your solution:
```bash
lc exec 1
# Alias: lc x 1
```

## Configuration

You can customize `leetcode-cli` by editing the configuration file located at `~/.config/leetcode-cli/config.json`.

### TUI layout
```json
{
    "tui_main_width": "50%",
    "tui_theme": "solarized-light",
    "tui_tmux": true,
    "drive_folder_id": "1AbC...",
    "rclone_remote": "gdrive",
    "data_sync": "drive"
}
```
`tui_main_width` is the width of the main pane. `drive_folder_id`, `rclone_remote` and `data_sync` configure the recent-list sync (see above). `tui_theme` is any Textual built-in theme (default `solarized-light`; others include `solarized-dark`, `gruvbox`, `nord`, `dracula`, `catppuccin-latte`, `textual-dark`). Set `tui_tmux` to `false` to always use the single-window mode.

### Data Directory
The tool's state (`recent.json`, `lists.json`, `index.json`) lives in `~/.config/leetcode-cli` by default. Change it with `data_dir` in `config.json`, or for one run with the `LC_DATA_DIR` environment variable:

```json
{
    "data_dir": "/path/to/your/custom/directory"
}
```

## Dependencies
- `typer`: For CLI parsing
- `rich`: For beautiful terminal output
- `textual`: For the TUI panes
- `rclone` (optional, external): For syncing the recent list and lists through Google Drive
- `requests` & `curl-cffi`: For HTTP requests and avoiding Cloudflare checks
- `browser-cookie3`: For seamless browser authentication 
- `beautifulsoup4` & `markdownify`: For HTML parsing and markdown rendering

## License
MIT License
