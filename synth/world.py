"""World model for the synthetic day.

Instead of fixed lists, this builds *coherent* content:

  * Projects with a file tree per stack (python-ml, python-web, node, c, rust).
    Editors move between files with locality (module <-> its test, neighbours).
  * Problems: a parameterized error (thousands of distinct messages), a few
    suspect files, a shell command, and a set of web pages whose titles and URLs
    are derived from that error and library, so a stuck session reads like a
    real debugging story.
  * Browsing topics (course site, research, news, video, jobs, shopping, social)
    that stay on-topic within a segment.

Everything is fabricated. Terminal errors are never stored as text downstream:
only a hash (`error_sig`) and the exception class name (`error_type`).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


# ------------------------------------------------------------------ helpers
def slug(s: str, n: int = 64) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n].strip("-")


def short_hash(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:8]


def error_sig(text: str) -> str:
    return short_hash(text)


def file_id(project: str, path: str) -> str:
    return short_hash(f"{project}/{path}")


_INTS = {"a": (2, 512), "b": (2, 512), "c": (2, 512), "d": (2, 512),
         "n": (64, 16384), "port": (3000, 8999)}


def fill(tpl: str, rng, vocab: dict | None = None, **fixed) -> str:
    vocab = vocab or {}

    def sub(m):
        k = m.group(1)
        if k in fixed:
            return str(fixed[k])
        if k in vocab:
            return rng.choice(vocab[k])
        if k in _INTS:
            return str(rng.randint(*_INTS[k]))
        if k == "hex":
            return f"{rng.randrange(0x80, 0xff):02x}"
        return m.group(0)
    return re.sub(r"\{(\w+)\}", sub, tpl)


FIRST = ["Alex", "Priya", "Marcus", "Sofia", "Dev", "Hannah", "Wei", "Carlos", "Amara", "Noah",
         "Isha", "Liam", "Mei", "Omar", "Zoe", "Ravi"]
LAST = ["Rivera", "Patel", "Chen", "Nguyen", "Okafor", "Schmidt", "Kim", "Silva", "Haddad",
        "Brooks", "Iyer", "Novak"]


def person(rng) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


CTX = ["after upgrading", "inside a loop", "with multiprocessing", "on Apple Silicon",
       "in a Docker container", "when loading a CSV", "only in production", "after a fresh install",
       "during training", "with async/await", "when running tests", "in a virtualenv",
       "after switching branches", "on the second run"]
TEST_NAMES = ["test_ema_smoothing", "test_rolling_window", "test_threshold", "test_parse",
              "test_empty_input", "test_roundtrip", "test_edge_case", "test_batch_shapes"]


# ------------------------------------------------------------------ libraries
def _lib(docs, title, syms, org=None, repo=None):
    return dict(docs=docs, title=title, syms=syms, org=org, repo=repo)


LIBS = {
    "numpy": _lib("https://numpy.org/doc/stable/reference/generated/numpy.{sym}.html",
                  "numpy.{sym} — NumPy v1.26 Manual",
                  ["convolve", "reshape", "where", "concatenate", "argmax", "einsum", "stack", "clip", "linalg.norm"], "numpy"),
    "pandas": _lib("https://pandas.pydata.org/docs/reference/api/pandas.{sym}.html",
                   "pandas.{sym} — pandas 2.2 documentation",
                   ["DataFrame.merge", "DataFrame.groupby", "Series.rolling", "read_csv", "concat", "DataFrame.apply", "to_datetime"], "pandas-dev"),
    "torch": _lib("https://pytorch.org/docs/stable/generated/torch.{sym}.html",
                  "torch.{sym} — PyTorch 2.3 documentation",
                  ["nn.Linear", "cat", "no_grad", "optim.Adam", "nn.CrossEntropyLoss", "Tensor.to", "utils.data.DataLoader"], "pytorch"),
    "scikit-learn": _lib("https://scikit-learn.org/stable/modules/generated/sklearn.{sym}.html",
                         "sklearn.{sym} — scikit-learn 1.4 documentation",
                         ["linear_model.LogisticRegression", "metrics.f1_score", "model_selection.train_test_split",
                          "preprocessing.StandardScaler", "ensemble.RandomForestClassifier"], "scikit-learn"),
    "transformers": _lib("https://huggingface.co/docs/transformers/main_classes/{sym}",
                         "{sym} — Transformers docs", ["tokenizer", "trainer", "model", "pipelines", "logging"], "huggingface"),
    "matplotlib": _lib("https://matplotlib.org/stable/api/_as_gen/matplotlib.{sym}.html",
                       "matplotlib.{sym} — Matplotlib 3.8 documentation",
                       ["pyplot.plot", "pyplot.subplots", "axes.Axes.hist", "figure.Figure.savefig"], "matplotlib"),
    "fastapi": _lib("https://fastapi.tiangolo.com/tutorial/{sym}/", "{sym} - FastAPI",
                    ["path-params", "query-params", "body", "dependencies", "security", "sql-databases"], "tiangolo"),
    "sqlalchemy": _lib("https://docs.sqlalchemy.org/en/20/orm/{sym}.html", "{sym} — SQLAlchemy 2.0 Documentation",
                       ["quickstart", "session_basics", "queryguide/select", "relationships"], "sqlalchemy"),
    "pydantic": _lib("https://docs.pydantic.dev/latest/concepts/{sym}/", "{sym} - Pydantic",
                     ["models", "validators", "fields", "serialization"], "pydantic"),
    "requests": _lib("https://requests.readthedocs.io/en/latest/user/{sym}/", "{sym} — Requests documentation",
                     ["quickstart", "advanced", "authentication"], "psf"),
    "pytest": _lib("https://docs.pytest.org/en/stable/how-to/{sym}.html", "How to use {sym} — pytest documentation",
                   ["fixtures", "parametrize", "monkeypatch", "tmp_path"], "pytest-dev"),
    "react": _lib("https://react.dev/reference/react/{sym}", "{sym} – React",
                  ["useEffect", "useState", "useMemo", "useRef", "useContext"], "facebook"),
    "redux-toolkit": _lib("https://redux-toolkit.js.org/api/{sym}", "{sym} | Redux Toolkit",
                          ["createSlice", "configureStore", "createAsyncThunk"], "reduxjs", "redux-toolkit"),
    "axios": _lib("https://axios-http.com/docs/{sym}", "{sym} | Axios Docs",
                  ["req_config", "interceptors", "handling_errors"], "axios"),
    "express": _lib("https://expressjs.com/en/4x/api.html#{sym}", "Express 4.x - API Reference",
                    ["app.use", "router", "req.params"], "expressjs"),
    "jest": _lib("https://jestjs.io/docs/{sym}", "{sym} · Jest",
                 ["expect", "mock-functions", "asynchronous", "setup-teardown"], "jestjs"),
    "typescript": _lib("https://www.typescriptlang.org/docs/handbook/{sym}.html", "TypeScript: Handbook - {sym}",
                       ["generics", "utility-types", "2/narrowing", "2/everyday-types"], "microsoft"),
    "vite": _lib("https://vitejs.dev/guide/{sym}", "{sym} | Vite", ["env-and-mode", "features", "build"], "vitejs"),
    "libc": _lib("https://man7.org/linux/man-pages/man3/{sym}.3.html", "{sym}(3) - Linux manual page",
                 ["malloc", "free", "pthread_create", "memcpy", "strncpy", "fork", "waitpid", "pthread_mutex_lock"]),
    "gnu-make": _lib("https://www.gnu.org/software/make/manual/html_node/{sym}.html", "GNU make: {sym}",
                     ["Rules", "Implicit-Rules", "Automatic-Variables"]),
    "valgrind": _lib("https://valgrind.org/docs/manual/{sym}.html", "Valgrind: {sym}", ["mc-manual", "quick-start"]),
    "std": _lib("https://doc.rust-lang.org/std/{sym}/index.html", "std::{sym} - Rust",
                ["vec", "collections", "sync", "option", "result", "fmt"], "rust-lang", "rust"),
    "tokio": _lib("https://docs.rs/tokio/latest/tokio/{sym}/index.html", "tokio::{sym} - Rust",
                  ["sync", "task", "time", "net"], "tokio-rs"),
    "serde": _lib("https://serde.rs/{sym}.html", "{sym} · Serde",
                  ["derive", "attributes", "enum-representations"], "serde-rs"),
}

# ------------------------------------------------------------------ stacks
PY_ERRORS = [
    ("ModuleNotFoundError", "No module named '{mod}'", "python"),
    ("ImportError", "cannot import name '{sym}' from '{lib}'", "python"),
    ("ValueError", "shapes ({a},{b}) and ({c},{d}) not aligned", "python"),
    ("KeyError", "'{col}'", "python"),
    ("TypeError", "unsupported operand type(s) for +: 'int' and 'str'", "python"),
    ("AttributeError", "'NoneType' object has no attribute '{attr}'", "python"),
    ("RuntimeError", "CUDA out of memory. Tried to allocate {n} MiB", "python"),
    ("FileNotFoundError", "[Errno 2] No such file or directory: '{path}'", "python"),
    ("IndexError", "index {a} is out of bounds for axis 0 with size {b}", "python"),
    ("AssertionError", "FAILED tests/test_{tmod}.py::{tname} - assert {a} == {b}", "pytest"),
    ("ZeroDivisionError", "division by zero", "python"),
    ("JSONDecodeError", "Expecting value: line {a} column {b} (char 0)", "python"),
    ("UnicodeDecodeError", "'utf-8' codec can't decode byte 0x{hex} in position {a}: invalid start byte", "python"),
]
PY_VOCAB = {
    "mod": ["sklearn", "cv2", "yaml", "dotenv", "tqdm", "seaborn", "transformers", "torchvision",
            "jwt", "psycopg2", "bs4", "nltk"],
    "col": ["timestamp", "user_id", "label", "text", "window_start", "event_type", "score"],
    "attr": ["shape", "values", "split", "item", "cuda", "group", "strip"],
    "path": ["data/train.csv", "configs/default.yaml", "checkpoints/best.pt", "logs/run1.json",
             "data/raw/events.jsonl"],
}

STACKS = {
    "py_ml": dict(
        lang="python", libs=["numpy", "pandas", "torch", "scikit-learn", "transformers", "matplotlib", "pytest"],
        modules=["data_loader", "tokenizer", "model", "trainer", "metrics", "features", "preprocess",
                 "windowing", "scoring", "inference", "utils", "calibration"],
        entry=["train", "evaluate", "run_pipeline"],
        projects=["nlp-hw{n}", "sentiment-classifier", "activity-state-detector", "rag-playground",
                  "text-summarizer", "tweet-embeddings", "speech-commands"],
        errors=PY_ERRORS, vocab=PY_VOCAB,
        cmds={"python": ["python {entry}.py", "python {entry}.py --epochs {k}", "python -m {pkg}.{m}"],
              "pytest": ["pytest tests/test_{m}.py -x", "pytest -q", "pytest tests/test_{m}.py -k {m}"]},
        tasks=["read a CSV file", "merge two dataframes", "parse command line arguments", "sort a dict by value",
               "deep copy an object", "plot a confusion matrix", "set a random seed", "save a model checkpoint"]),
    "py_web": dict(
        lang="python", libs=["fastapi", "sqlalchemy", "pydantic", "requests", "pytest"],
        modules=["routes", "models", "schemas", "auth", "db", "crud", "settings", "middleware", "client"],
        entry=["main", "seed_db"],
        projects=["inventory-api", "todo-fastapi", "blog-backend", "auth-service", "booking-api"],
        errors=PY_ERRORS + [("OperationalError", "(psycopg2.OperationalError) connection to server at \"localhost\" ({port}) failed", "python"),
                            ("ValidationError", "{a} validation errors for {tmod}", "python")],
        vocab=PY_VOCAB,
        cmds={"python": ["uvicorn {pkg}.main:app --reload", "python {entry}.py", "alembic upgrade head"],
              "pytest": ["pytest tests/test_{m}.py -x", "pytest -q"]},
        tasks=["add a foreign key", "hash a password", "paginate query results", "mock an HTTP request",
               "validate an email field", "run a migration", "set CORS headers"]),
    "node": dict(
        lang="javascript", libs=["react", "redux-toolkit", "axios", "express", "jest", "typescript", "vite"],
        modules=["dashboard", "chart", "navbar", "sidebar", "login", "table", "modal", "toast", "profile"],
        entry=["server"],
        projects=["web-dashboard", "portfolio-site", "chat-app-react", "expense-tracker", "recipe-finder"],
        errors=[
            ("TypeError", "Cannot read properties of undefined (reading '{prop}')", "node"),
            ("ModuleNotFoundError", "Module not found: Error: Can't resolve '{mod}'", "npm"),
            ("Error", "listen EADDRINUSE: address already in use :::{port}", "node"),
            ("TS2322", "Type '{ta}' is not assignable to type '{tb}'", "npm"),
            ("ReferenceError", "{ident} is not defined", "node"),
            ("SyntaxError", "Unexpected token '<'", "node"),
            ("JestAssertionError", "expect(received).toBe(expected) // Object.is equality", "npm"),
            ("Error", "ENOENT: no such file or directory, open '{path}'", "node"),
        ],
        vocab={"prop": ["map", "length", "id", "data", "user", "items", "name"],
               "mod": ["lodash", "axios", "react-router-dom", "@mui/material", "dayjs", "zod"],
               "ta": ["string", "number", "User[]", "Props", "Date"], "tb": ["number", "string", "User", "Item", "boolean"],
               "ident": ["process", "fetchData", "useAuth", "STORE", "handleClick"],
               "path": ["./config.json", "public/data.json", ".env.local"]},
        cmds={"node": ["node server.js", "npx ts-node src/server.ts"],
              "npm": ["npm test", "npm run dev", "npm run build", "npx tsc --noEmit"]},
        tasks=["debounce a function", "center a div", "fetch data in useEffect", "deep clone an object",
               "format a date", "persist state to localStorage", "type a React prop"]),
    "c": dict(
        lang="c", libs=["libc", "gnu-make", "valgrind"],
        modules=["scheduler", "queue", "memory", "thread_pool", "parser", "shell", "cache", "list"],
        entry=["main"],
        projects=["os-lab{n}", "shell-impl", "malloc-lab", "thread-pool", "mini-http-server"],
        errors=[
            ("error", "'{ctype}' undeclared (first use in this function)", "make"),
            ("LinkerError", "undefined reference to `{fn}'", "make"),
            ("SIGSEGV", "Segmentation fault (core dumped)", "run"),
            ("AssertionFailed", "Assertion `{expr}' failed.", "run"),
            ("warning", "implicit declaration of function '{fn}'", "make"),
            ("InvalidRead", "Invalid read of size {n}", "run"),
            ("MakeError", "make: *** [{target}] Error 1", "make"),
        ],
        vocab={"ctype": ["proc_t", "node_t", "queue_t", "task_t", "buf_t"],
               "fn": ["enqueue", "sched_yield_all", "list_remove", "pool_submit", "parse_line"],
               "expr": ["q->len > 0", "ptr != NULL", "idx < cap", "lock->owner == self"],
               "target": ["scheduler.o", "main", "test_queue", "all"]},
        cmds={"make": ["make", "make test", "make clean && make"], "run": ["./{bin}", "valgrind ./{bin}", "gdb ./{bin}"]},
        tasks=["free a linked list", "use pthread_mutex", "read a file line by line", "parse a string with strtok",
               "avoid a memory leak", "implement a ring buffer"]),
    "rust": dict(
        lang="rust", libs=["std", "tokio", "serde"],
        modules=["store", "parser", "cache", "net", "config", "ray", "scene", "cli"],
        entry=["main"],
        projects=["rust-kv-store", "ray-tracer", "ownership-lab", "cli-todo", "mini-grep"],
        errors=[
            ("E0382", "borrow of moved value: `{var}`", "cargo"),
            ("E0308", "mismatched types: expected `{ta}`, found `{tb}`", "cargo"),
            ("E0599", "no method named `{fn}` found for struct `{ctype}`", "cargo"),
            ("E0499", "cannot borrow `{var}` as mutable more than once at a time", "cargo"),
            ("Panic", "called `Option::unwrap()` on a `None` value", "cargo"),
            ("E0277", "the trait bound `{ctype}: Clone` is not satisfied", "cargo"),
        ],
        vocab={"var": ["buf", "state", "items", "config", "line"], "ta": ["usize", "String", "&str", "Vec<u8>"],
               "tb": ["i32", "&String", "Option<String>", "u64"], "fn": ["push_str", "get_mut", "parse", "iter"],
               "ctype": ["Store", "Scene", "Config", "Cache"]},
        cmds={"cargo": ["cargo build", "cargo test", "cargo run", "cargo check"]},
        tasks=["return a reference from a function", "iterate over a HashMap", "parse a string to an integer",
               "share state between threads", "read a file into a String"]),
}
_STACK_W = [("py_ml", .32), ("py_web", .16), ("node", .2), ("c", .16), ("rust", .16)]


# ------------------------------------------------------------------ projects
@dataclass
class File:
    path: str
    kind: str          # impl | test | entry | config | doc
    module: str

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass
class Project:
    name: str
    stack: str
    files: list
    libs: list
    modules: list
    pkg: str = ""


def _wchoice(rng, pairs):
    r, acc = rng.random() * sum(w for _, w in pairs), 0.0
    for v, w in pairs:
        acc += w
        if r <= acc:
            return v
    return pairs[-1][0]


def make_project(rng) -> Project:
    key = _wchoice(rng, _STACK_W)
    st = STACKS[key]
    name = fill(rng.choice(st["projects"]), rng, {}, n=rng.randint(1, 6))
    mods = rng.sample(st["modules"], k=rng.randint(5, min(8, len(st["modules"]))))
    pkg = slug(name).replace("-", "_")
    f: list[File] = []
    if key.startswith("py"):
        f += [File(f"src/{pkg}/{m}.py", "impl", m) for m in mods]
        f += [File(f"tests/test_{m}.py", "test", m) for m in mods if rng.random() < 0.7]
        f += [File(f"{e}.py", "entry", e) for e in st["entry"]]
        f += [File("README.md", "doc", ""), File("requirements.txt", "config", ""), File("config.yaml", "config", "")]
    elif key == "node":
        comps = [m[0].upper() + m[1:] for m in mods[:5]]
        f += [File(f"src/components/{c}.tsx", "impl", c) for c in comps]
        f += [File(f"src/components/{c}.test.tsx", "test", c) for c in comps if rng.random() < 0.6]
        f += [File("src/api.ts", "impl", "api"), File("src/store.ts", "impl", "store"),
              File("src/App.tsx", "impl", "App"), File("server.js", "entry", "server"),
              File("package.json", "config", "")]
    elif key == "c":
        f += [File(f"{m}.c", "impl", m) for m in mods] + [File(f"{m}.h", "impl", m) for m in mods[:4]]
        f += [File(f"tests/test_{m}.c", "test", m) for m in mods if rng.random() < 0.5]
        f += [File("main.c", "entry", "main"), File("Makefile", "config", "")]
    else:
        f += [File(f"src/{m}.rs", "impl", m) for m in mods]
        f += [File(f"tests/{m}_test.rs", "test", m) for m in mods if rng.random() < 0.5]
        f += [File("src/main.rs", "entry", "main"), File("Cargo.toml", "config", "")]
    libs = rng.sample(st["libs"], k=min(len(st["libs"]), rng.randint(2, 4)))
    return Project(name, key, f, libs, mods, pkg)


def next_file(rng, proj: Project, cur: File) -> File:
    impl = [f for f in proj.files if f.kind in ("impl", "entry")]
    tests = [f for f in proj.files if f.kind == "test"]
    cfg = [f for f in proj.files if f.kind in ("config", "doc")]
    r = rng.random()
    if cur.kind in ("impl", "entry"):
        pair = [t for t in tests if t.module == cur.module]
        if r < 0.35 and pair:
            return pair[0]
        if r < 0.90:
            return rng.choice([f for f in impl if f is not cur] or impl)
        return rng.choice(cfg or impl)
    if cur.kind == "test":
        pair = [f for f in impl if f.module == cur.module]
        return pair[0] if r < 0.7 and pair else rng.choice(impl)
    return rng.choice(impl)


def cmd_string(rng, proj: Project, kind: str, module: str | None = None) -> str:
    st = STACKS[proj.stack]
    tpl = rng.choice(st["cmds"].get(kind) or [kind])
    return fill(tpl, rng, {}, m=module or rng.choice(proj.modules), entry=rng.choice(st["entry"]),
                pkg=proj.pkg, bin=slug(proj.name)[:12], k=rng.choice([1, 3, 5, 10]))


def terminal_title(project: str, cmd: str | None = None) -> str:
    return f"{project} — {cmd + ' — ' if cmd else '-zsh — '}128×34"


# ------------------------------------------------------------------ web pages
def _so(rng, st, etype, short):
    qid = rng.randint(10_000_000, 78_000_000)
    ctx, lang = rng.choice(CTX), st["lang"]
    title = rng.choice([
        f"{etype}: {short}",
        f"{lang} - {etype}: {short} {ctx}",
        f"How to fix \"{etype}: {short}\" {ctx}?",
        f"{lang} - What does \"{short}\" mean?",
    ]) + " - Stack Overflow"
    return dict(url=f"https://stackoverflow.com/questions/{qid}/{slug(title.replace(' - Stack Overflow', ''))}", title=title)


def _gh(rng, lib, etype, short):
    info = LIBS[lib]
    if not info["org"]:
        return None
    n = rng.randint(100, 24000)
    repo = info["repo"] or lib
    return dict(url=f"https://github.com/{info['org']}/{repo}/issues/{n}",
                title=f"{etype}: {short} {rng.choice(CTX)} · Issue #{n} · {info['org']}/{repo}")


def _blog(rng, lib, etype, short):
    dom, suffix = rng.choice([("medium.com", f"by {person(rng)} | Medium"), ("dev.to", "DEV Community"),
                              ("realpython.com", "Real Python"), ("freecodecamp.org", "freeCodeCamp.org"),
                              ("towardsdatascience.com", "Towards Data Science")])
    t = f"How to Fix {etype} in {lib} ({short[:34]})"
    return dict(url=f"https://{dom}/{slug(t)}-{rng.randrange(16**6):06x}", title=f"{t} | {suffix}")


def _reddit(rng, st, etype):
    sub = rng.choice({"python": ["learnpython", "MachineLearning"], "javascript": ["reactjs", "webdev"],
                      "c": ["C_Programming", "cprogramming"], "rust": ["rust", "learnrust"]}[st["lang"]])
    t = f"{etype} {rng.choice(CTX)}"
    return dict(url=f"https://www.reddit.com/r/{sub}/comments/{rng.randrange(16**6):06x}/{slug(t)}/",
                title=f"{t} : r/{sub}")


def doc_page(rng, lib, sym=None):
    info = LIBS[lib]
    sym = sym or rng.choice(info["syms"])
    return dict(url=info["docs"].format(sym=sym), title=info["title"].format(sym=sym))


def lookup_page(rng, proj: Project) -> dict:
    """Everyday lookups while things are going fine."""
    if rng.random() < 0.7:
        return doc_page(rng, rng.choice(proj.libs))
    st = STACKS[proj.stack]
    t = f"How to {rng.choice(st['tasks'])} in {st['lang']} - Stack Overflow"
    return dict(url=f"https://stackoverflow.com/questions/{rng.randint(10_000_000, 78_000_000)}/{slug(t[:-17])}", title=t)


@dataclass
class Problem:
    etype: str
    msg: str
    sig: str
    sig2: str | None
    cmd_kind: str
    cmd_str: str
    lib: str
    suspects: list
    pages: list
    browser: str
    module: str = ""
    silent: bool = False        # logic bug: commands run, output is just wrong
    symptom: str = ""


SYMPTOMS = ["returns None", "gives the wrong output", "never terminates", "is off by one",
            "does not update state", "has the wrong shape", "is much slower than expected"]


def _symptom_pages(rng, st, lib, sym, symptom):
    lang = st["lang"]
    out = []
    for _ in range(rng.randint(2, 3)):
        qid = rng.randint(10_000_000, 78_000_000)
        t = rng.choice([f"{lang} - {lib}.{sym} {symptom}", f"Why {sym} {symptom} in {lib}?",
                        f"{lang} - function {symptom} {rng.choice(CTX)}"]) + " - Stack Overflow"
        out.append(dict(url=f"https://stackoverflow.com/questions/{qid}/{slug(t[:-17])}", title=t))
    t = f"Debugging when your code {symptom} | {rng.choice(['Real Python', 'DEV Community', 'freeCodeCamp.org'])}"
    out.append(dict(url=f"https://dev.to/{slug(t)}-{rng.randrange(16**5):05x}", title=t))
    out.append(doc_page(rng, lib, sym))
    return out


def make_problem(rng, proj: Project, silent: bool = False) -> Problem:
    st = STACKS[proj.stack]
    if silent:
        impl = [f for f in proj.files if f.kind in ("impl", "entry")]
        suspects = rng.sample(impl, k=min(len(impl), rng.randint(1, 2)))
        lib = rng.choice(proj.libs)
        sym = rng.choice(LIBS[lib]["syms"])
        symptom = rng.choice(SYMPTOMS)
        kind = rng.choice([k for k in st["cmds"]])
        return Problem(None, f"{sym} {symptom}", "", None, kind,
                       cmd_string(rng, proj, kind, suspects[0].module or None), lib, suspects,
                       _symptom_pages(rng, st, lib, sym, symptom), rng.choice(["Google Chrome", "Safari"]),
                       suspects[0].module, True, symptom)
    etype, tpl, kind = rng.choice(st["errors"])
    impl = [f for f in proj.files if f.kind in ("impl", "entry")]
    tests = [f for f in proj.files if f.kind == "test"]
    suspects = rng.sample(impl, k=min(len(impl), rng.randint(1, 2)))
    if tests and (kind in ("pytest",) or rng.random() < 0.4):
        suspects.append(next((t for t in tests if t.module == suspects[0].module), rng.choice(tests)))
    primary = suspects[0]
    lib = rng.choice(proj.libs)
    sym = rng.choice(LIBS[lib]["syms"])
    fixed = dict(lib=lib, sym=sym, tmod=primary.module or "core", tname=rng.choice(TEST_NAMES))
    msg = fill(tpl, rng, st["vocab"], **fixed)
    msg2 = fill(tpl, rng, st["vocab"], **fixed)
    short = msg if len(msg) <= 70 else msg[:67] + "..."
    pages = [_so(rng, st, etype, short) for _ in range(rng.randint(2, 3))]
    for p in (_gh(rng, lib, etype, short), _blog(rng, lib, etype, short),
              _reddit(rng, st, etype) if rng.random() < 0.4 else None):
        if p:
            pages.append(p)
    pages += [doc_page(rng, lib, sym)] + ([doc_page(rng, lib)] if rng.random() < 0.5 else [])
    return Problem(etype, msg, error_sig(etype + msg), error_sig(etype + msg2) if msg2 != msg else None,
                   kind, cmd_string(rng, proj, kind, primary.module or None), lib, suspects, pages,
                   rng.choice(["Google Chrome", "Safari"]), primary.module)


def random_error(rng, proj: Project):
    """A one-off failure during normal work: (etype, cmd_kind, sig)."""
    st = STACKS[proj.stack]
    etype, tpl, kind = rng.choice(st["errors"])
    msg = fill(tpl, rng, st["vocab"], lib=rng.choice(proj.libs), sym="x", tmod=rng.choice(proj.modules),
               tname=rng.choice(TEST_NAMES))
    return etype, kind, error_sig(etype + msg)


# ------------------------------------------------------------------ everyday apps
COURSES = ["CSE 576 Topics in NLP", "CSE 471 Intro to AI", "CSE 543 Information Assurance",
           "CSE 430 Operating Systems", "MAT 343 Linear Algebra", "CSE 579 Knowledge Representation"]
CANVAS = ["Assignments", "Modules", "Grades", "Announcements", "Discussions", "Syllabus", "Quizzes"]
PAPERS = ["PRISM: Festina Lente Proactivity", "Attention Is All You Need",
          "Language Models are Few-Shot Learners", "Chain-of-Thought Prompting Elicits Reasoning",
          "ReAct: Synergizing Reasoning and Acting", "Toolformer: Language Models Can Teach Themselves to Use Tools",
          "Retrieval-Augmented Generation for Knowledge-Intensive NLP", "Calibrate Before Use: Improving Few-Shot Performance"]
_ADJ = ["Efficient", "Scalable", "Robust", "Calibrated", "Uncertainty-Aware", "Context-Aware"]
_NOUN = ["Prompting", "Retrieval", "Agents", "Fine-Tuning", "Decoding", "Evaluation"]
_TASK = ["Code Generation", "Question Answering", "Dialogue", "Summarization", "Tool Use", "Proactive Assistance"]
WIKI = ["Exponential smoothing", "Flow (psychology)", "Cost-sensitive learning", "Large language model",
        "Interruption science", "Beam search", "Cohen's kappa", "Logistic regression"]
HEADS = ["Researchers unveil", "Startup launches", "Study finds", "Developers debate", "Regulators weigh"]
THINGS = ["a faster open-source language model", "new limits on AI agents", "gains from smaller models",
          "a privacy-first desktop assistant", "the cost of context switching", "a rewrite of a popular build tool"]
ROLES = ["Software Engineer", "ML Engineer", "Data Scientist", "Backend Engineer", "Research Engineer"]
COMPANIES = ["Northwind Labs", "Acme Robotics", "Globex Systems", "Initech", "Umbrella Analytics", "Stark Cloud"]
PRODUCTS = ["USB-C hub 7-in-1", "mechanical keyboard", "laptop stand aluminum", "noise cancelling headphones",
            "27 inch monitor"]
SUBS = ["learnprogramming", "MachineLearning", "gradadmissions", "macapps", "cscareerquestions"]
VIDEO_TOPICS = ["Transformers", "Backpropagation", "Kalman filters", "Rust ownership", "Linux scheduling",
                "Vector databases", "Reinforcement learning"]
CHANNELS = ["general", "random", "announcements", "project-saip", "cse576-help", "study-group"]
WORKSPACES = ["ASU CSE", "Capstone Team", "ML Reading Group"]
TOPICS = [("course", .25), ("research", .2), ("news", .15), ("video", .15), ("jobs", .1), ("shopping", .05), ("social", .1)]


def paper_title(rng) -> str:
    if rng.random() < 0.5:
        return rng.choice(PAPERS)
    return f"{rng.choice(_ADJ)} {rng.choice(_NOUN)} for {rng.choice(_TASK)}"


def browse_page(rng, topic: str) -> dict:
    h = lambda: f"{rng.randrange(16**8):08x}"
    if topic == "course":
        c, s = rng.choice(COURSES), rng.choice(CANVAS)
        return dict(url=f"https://canvas.example.edu/courses/{rng.randint(10000, 99999)}/{slug(s)}", title=f"{s}: {c}")
    if topic == "research":
        r = rng.random()
        if r < .45:
            t = paper_title(rng)
            return dict(url=f"https://arxiv.org/abs/26{rng.randint(1, 11):02d}.{rng.randint(1000, 19999):05d}", title=f"[{t}]")
        if r < .75:
            w = rng.choice(WIKI)
            return dict(url=f"https://en.wikipedia.org/wiki/{w.replace(' ', '_')}", title=f"{w} - Wikipedia")
        t = paper_title(rng)
        return dict(url=f"https://www.semanticscholar.org/paper/{slug(t)}/{h()}", title=f"{t} | Semantic Scholar")
    if topic == "news":
        t = f"{rng.choice(HEADS)} {rng.choice(THINGS)}"
        return dict(url=f"https://news.ycombinator.com/item?id={rng.randint(40_000_000, 47_000_000)}", title=f"{t} | Hacker News")
    if topic == "video":
        t = f"{rng.choice(VIDEO_TOPICS)} explained in {rng.choice([5, 10, 12, 18, 25])} minutes"
        return dict(url=f"https://www.youtube.com/watch?v={rng.randrange(36**11):011x}"[:50], title=f"{t} - YouTube")
    if topic == "jobs":
        r, c = rng.choice(ROLES), rng.choice(COMPANIES)
        if rng.random() < .5:
            return dict(url=f"https://{slug(c)}.wd1.myworkdayjobs.com/en-US/external/job/{slug(r)}_{rng.randint(1000, 9999)}", title=f"{r} — {c} Careers")
        return dict(url=f"https://www.linkedin.com/jobs/view/{rng.randint(3_000_000_000, 4_200_000_000)}", title=f"{r} - {c} | LinkedIn")
    if topic == "shopping":
        p = rng.choice(PRODUCTS)
        return dict(url=f"https://www.amazon.com/dp/B0{h()[:8].upper()}", title=f"{p} - Amazon.com")
    sub = rng.choice(SUBS)                                  # social
    t = f"{rng.choice(['Anyone else', 'Advice on', 'PSA:', 'How do you'])} {rng.choice(THINGS)}?"
    return dict(url=f"https://www.reddit.com/r/{sub}/comments/{h()[:6]}/{slug(t)}/", title=f"{t} : r/{sub}")


def random_page(rng, libs=None) -> dict:
    """A plausible other tab, used for background peeks: the project's own docs or everyday browsing."""
    if libs and rng.random() < 0.5:
        return doc_page(rng, rng.choice(libs))
    return browse_page(rng, _wchoice(rng, TOPICS))


def mail_win(rng) -> dict:
    n = rng.randint(1200, 4800)
    return {"app": "Mail", "title": f"Inbox — iCloud ({n:,} messages, {rng.randint(0, 24)} unread)"}


def slack_win(rng) -> dict:
    ch = rng.choice(CHANNELS) if rng.random() < .7 else person(rng)
    return {"app": "Slack", "title": f"{ch} - {rng.choice(WORKSPACES)} - Slack"}


def pdf_win(rng) -> dict:
    r = rng.random()
    if r < .4:
        name = f"lecture-{rng.randint(1, 20):02d}-{slug(rng.choice(WIKI + VIDEO_TOPICS))}.pdf"
    elif r < .75:
        name = f"{rng.choice(LAST).lower()}{rng.choice([2023, 2024, 2025, 2026])}-{slug(paper_title(rng))[:34]}.pdf"
    else:
        name = f"assignment{rng.randint(1, 6)}.pdf"
    return {"app": "Preview", "title": name}


def zoom_win(rng) -> dict:
    return {"app": "zoom.us", "title": rng.choice(["Zoom Meeting", "Zoom Meeting", "Zoom Workplace"])}


# ------------------------------------------------------------------ non-coding work
WRITING_NAMES = ["CSE 576 Project Proposal", "Literature Review", "Methods Draft", "Cover Letter",
                 "Lab Report 3", "Thesis Chapter 2", "Statement of Purpose", "Final Paper Outline"]
SHEET_NAMES = ["midterm_results", "survey_responses", "sales_forecast", "experiment_logs", "gradebook",
               "budget_2026", "user_study_data"]
WRITING_PROBLEMS = ["structure the related work section", "cite a paper in APA format",
                    "reword the abstract to fit 150 words", "explain the evaluation metric clearly",
                    "number figure captions correctly", "write a transition between sections"]
LATEX_PROBLEMS = ["Undefined control sequence", "Missing $ inserted", "File not found: package .sty",
                  "Citation undefined in bibliography", "Overfull hbox in table", "float placement of figures"]
SHEET_PROBLEMS = ["VLOOKUP returns #N/A", "circular reference warning", "pivot table will not refresh",
                  "dates imported as text", "#REF! after deleting a column", "conditional formatting not applying"]
NB_PROBLEMS = ["merge returns duplicate rows", "groupby result has NaN values", "matplotlib plot not showing",
               "SettingWithCopyWarning on assignment", "kernel keeps dying", "KeyError after reading CSV"]
SHEET_TASKS = ["use VLOOKUP", "make a pivot table", "remove duplicates", "apply conditional formatting",
               "merge two sheets", "plot a histogram", "freeze header rows"]
STYLE_PAGES = [("https://owl.purdue.edu/owl/research_and_citation/apa_style/apa_formatting_and_style_guide/general_format.html",
                "General Format - Purdue OWL"),
               ("https://www.scribbr.com/apa-style/format/", "APA Format for Academic Papers | Scribbr"),
               ("https://www.thesaurus.com/browse/significant", "Significant Synonyms | Thesaurus.com"),
               ("https://en.wikipedia.org/wiki/Academic_writing", "Academic writing - Wikipedia")]


@dataclass
class Workspace:
    kind: str                 # writing | analysis
    name: str
    fmt: str                  # docs | overleaf | word | excel | numbers | jupyter | colab
    win: dict
    saves: bool               # file-backed formats emit save events
    libs: list = field(default_factory=list)


def make_workspace(rng, kind: str) -> Workspace:
    uid = f"{rng.randrange(16**20):020x}"
    if kind == "writing":
        name = rng.choice(WRITING_NAMES)
        fmt = rng.choice(["docs", "docs", "overleaf", "word"])
        if fmt == "docs":
            return Workspace(kind, name, fmt, {"app": "Google Chrome", "title": f"{name} - Google Docs",
                                               "url": f"https://docs.google.com/document/d/{uid}/edit"}, False)
        if fmt == "overleaf":
            return Workspace(kind, name, fmt, {"app": "Google Chrome",
                                               "title": f"{name} - Overleaf, Online LaTeX Editor",
                                               "url": f"https://www.overleaf.com/project/{uid[:24]}"}, False)
        return Workspace(kind, name, fmt, {"app": "Microsoft Word", "title": f"{name}.docx"}, True)
    name = rng.choice(SHEET_NAMES)
    fmt = rng.choice(["excel", "excel", "jupyter", "colab", "numbers"])
    if fmt == "excel":
        return Workspace(kind, name, fmt, {"app": "Microsoft Excel", "title": f"{name}.xlsx"}, True)
    if fmt == "numbers":
        return Workspace(kind, name, fmt, {"app": "Numbers", "title": name}, True)
    if fmt == "jupyter":
        return Workspace(kind, name, fmt, {"app": "Google Chrome", "title": f"{name}.ipynb - JupyterLab",
                                           "url": f"http://localhost:8888/lab/tree/{name}.ipynb"}, True,
                         ["pandas", "numpy", "matplotlib"])
    return Workspace(kind, name, fmt, {"app": "Google Chrome", "title": f"{name}.ipynb - Colab",
                                       "url": f"https://colab.research.google.com/drive/{uid[:33]}"}, False,
                     ["pandas", "numpy", "matplotlib"])


def nc_page(rng, ws: Workspace) -> dict:
    """Everyday lookups while non-coding work is going fine."""
    r = rng.random()
    if ws.kind == "writing":
        if r < .45:
            return browse_page(rng, "research")
        if r < .65:
            q = rng.choice(WIKI + VIDEO_TOPICS)
            return dict(url=f"https://scholar.google.com/scholar?q={slug(q)}", title=f"{q} - Google Scholar")
        url, title = rng.choice(STYLE_PAGES)
        return dict(url=url, title=title)
    if ws.libs and r < .6:
        return doc_page(rng, rng.choice(ws.libs))
    t = f"How to {rng.choice(SHEET_TASKS)} in {'Excel' if not ws.libs else 'pandas'}"
    if rng.random() < .5:
        return dict(url=f"https://support.microsoft.com/en-us/office/{slug(t)}", title=f"{t} - Microsoft Support")
    return dict(url=f"https://stackoverflow.com/questions/{rng.randint(10_000_000, 78_000_000)}/{slug(t)}",
                title=f"{t} - Stack Overflow")


@dataclass
class NCProblem:
    topic: str
    pages: list
    browser: str
    kind: str                 # writing | analysis
    fmt: str


def make_nc_problem(rng, ws: Workspace) -> NCProblem:
    if ws.kind == "writing":
        topic = rng.choice(LATEX_PROBLEMS if ws.fmt == "overleaf" else WRITING_PROBLEMS)
    else:
        topic = rng.choice(NB_PROBLEMS if ws.libs else SHEET_PROBLEMS)
    tag = {"docs": "google-docs", "overleaf": "latex", "word": "ms-word", "excel": "excel",
           "numbers": "numbers", "jupyter": "pandas", "colab": "pandas"}[ws.fmt]
    pages = []
    for _ in range(rng.randint(2, 3)):
        qid = rng.randint(10_000_000, 78_000_000)
        t = f"{tag} - {topic}? - Stack Overflow"
        pages.append(dict(url=f"https://stackoverflow.com/questions/{qid}/{slug(topic)}", title=t))
    if ws.fmt == "overleaf":
        pages.append(dict(url=f"https://tex.stackexchange.com/questions/{rng.randint(100000, 700000)}/{slug(topic)}",
                          title=f"{topic} - TeX - LaTeX Stack Exchange"))
    if ws.kind == "analysis" and not ws.libs:
        pages.append(dict(url=f"https://support.microsoft.com/en-us/office/{slug(topic)}",
                          title=f"{topic} - Microsoft Support"))
    site = rng.choice(["Scribbr", "Medium", "Grammarly Blog", "Real Python", "Towards Data Science"])
    pages.append(dict(url=f"https://www.{slug(site)}.com/{slug(topic)}-{rng.randrange(16**5):05x}",
                      title=f"{topic[0].upper() + topic[1:]}: a step-by-step guide | {site}"))
    pages.append(dict(url=f"https://www.reddit.com/r/{rng.choice(['AskAcademia', 'excel', 'LaTeX', 'datascience', 'gradschool'])}"
                          f"/comments/{rng.randrange(16**6):06x}/{slug(topic)}/", title=f"{topic} : r/help"))
    if ws.libs:
        pages.append(doc_page(rng, rng.choice(ws.libs)))
    else:
        pages.append(rng.choice([dict(url=u, title=t) for u, t in STYLE_PAGES]))
    return NCProblem(topic, pages, rng.choice(["Google Chrome", "Safari"]), ws.kind, ws.fmt)


def notes_win(rng) -> dict:
    return {"app": "Notes", "title": f"{rng.choice(COURSES)} \u2014 notes"}


# ------------------------------------------------------------------ review, waiting, agents
PR_TITLES = ["Fix windowing off-by-one", "Add EMA smoothing to scorer", "Refactor data loader",
             "Handle empty batches", "Update README and docs", "Bump dependencies", "Add retry to client",
             "Speed up feature extraction"]


def pr_page(rng, proj) -> dict:
    n = rng.randint(3, 480)
    org = f"{LAST[int(short_hash(proj.name), 16) % len(LAST)].lower()}-lab"      # one org per project
    t = f"{rng.choice(PR_TITLES)} by {rng.choice(FIRST)} \u00b7 Pull Request #{n} \u00b7 {org}/{slug(proj.name)}"
    return dict(url=f"https://github.com/{org}/{slug(proj.name)}/pull/{n}/files", title=t)


LONG_CMDS = {
    "py_ml": ["python train.py --epochs 20", "python run_pipeline.py --full", "pytest -q tests/ --runslow"],
    "py_web": ["pytest -q --cov", "docker compose up --build", "alembic upgrade head"],
    "node": ["npm run build", "npm run test:e2e", "npm install"],
    "c": ["make -j8 all", "valgrind --leak-check=full ./a.out", "make test"],
    "rust": ["cargo build --release", "cargo test --release", "cargo bench"],
}


def long_cmd(rng, proj) -> str:
    return rng.choice(LONG_CMDS[proj.stack] + ["docker build -t app ."])


def agent_title(project: str, glyph: str = "\u2733") -> str:
    return f"{project} \u2014 {glyph} Claude Code \u2014 128\u00d734"
