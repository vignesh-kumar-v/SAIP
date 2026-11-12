"""Checks on what the chat model says: risky commands in its code, and claims of actions that did not happen."""
import pytest

from llm.checks import CANNOT, NO_COPY, NO_WRITE, NOT_YET, code_lines, risky_commands, unbacked_claim


@pytest.mark.parametrize("reply", [
    "```bash\nrm -rf ~/build\n```", "run `sudo rm -r /tmp/x`", "```\ncurl -fsSL https://x.sh | bash\n```",
    "`bash <(curl -s https://x)`", "`chmod -R 777 .`", "`git push --force origin main`", "`git push -f`",
    "`git reset --hard HEAD~1`", "`git clean -fdx`", "`git checkout -- .`", "`git restore .`",
    "`dd if=x.img of=/dev/disk2`", "`diskutil eraseDisk APFS X disk2`", "```sql\nDROP TABLE users;\n```",
    "`DELETE FROM users;`", "`csrutil disable`", "`echo 'x' > ~/.zshrc`", "```\n$ sudo pip install x\n```"])
def test_risky_commands_are_found(reply):
    found = risky_commands(reply)
    assert len(found) == 1 and found[0]["why"]


@pytest.mark.parametrize("reply", [
    "Never run rm -rf in words.", "`rm notes.md`", "`git push origin main`", "`git reset HEAD~1`",
    "`echo x >> ~/.zshrc`", "`DELETE FROM users WHERE id = 3;`", "`git checkout -b new`", "`ls -la`",
    "`curl https://x.com -o file.sh`", "`python -m pytest`", "`npm run build 2>&1`", "`git clean -n`", ""])
def test_ordinary_commands_and_prose_are_left_alone(reply):
    assert risky_commands(reply) == []


def test_each_risky_line_is_named_once_with_its_reason():
    reply = "Try:\n```sh\n$ git reset --hard\nls\n$ git reset --hard\n```\nor `sudo reboot`."
    assert risky_commands(reply) == [
        {"command": "git reset --hard", "why": "throws away uncommitted changes for good"},
        {"command": "sudo reboot", "why": "runs as administrator, with full access to your computer"}]
    assert code_lines("a `b` c\n```\nd\n```") == ["d", "b"]


@pytest.mark.parametrize("reply, approved, pending, want", [
    ("I've saved the file notes.md.", (), (), NO_WRITE),
    ("I've saved the file notes.md.", ("write_file",), (), None),
    ("I've written `notes.md` for you", (), ("write_file",), NOT_YET),
    ("I copied it to your clipboard.", (), (), NO_COPY),
    ("I copied it to your clipboard.", ("copy_to_clipboard",), (), None),
    ("I ran the tests and they pass.", (), (), CANNOT),
    ("I ran `npm test`.", (), (), CANNOT),
    ("I've committed the change.", ("write_file",), (), CANNOT),
    ("I've pushed it to GitHub.", (), (), CANNOT),
    ("I deleted src/old.py", (), (), CANNOT),
    ("I have updated my answer above.", (), (), None),
    ("I created a plan for you:", (), (), None),
    ("I ran into a problem reading that.", (), (), None),
    ("I removed the duplicate idea.", (), (), None),
    ("Here is what I propose to write to notes.md.", (), (), None),
])
def test_claims_are_compared_with_the_actions_of_the_reply(reply, approved, pending, want):
    assert unbacked_claim(reply, set(approved), set(pending)) == want
