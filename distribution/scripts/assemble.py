"""Assemble Agent-EDA source from a pinned, clean Hermes reference; no deployment."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from typing import Callable


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_adapter_bytes(source: Path, entry: dict, relative_path: str) -> bytes:
    """Verify a core adapter independent of Windows checkout line endings."""
    content = source.read_bytes().replace(b'\r\n', b'\n')
    if hashlib.sha256(content).hexdigest() != entry['product_sha256']:
        raise ValueError(f'core adapter is undeclared or changed: {relative_path}')
    return content


def git(reference: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(reference), *args], text=True).strip()


def checked_source(reference: Path, lock: dict, selected: str) -> str:
    expected = lock[selected]['commit']
    actual = git(reference, 'rev-parse', f"{lock[selected]['tag']}^{{commit}}")
    if actual != expected:
        raise ValueError(f'{selected} tag does not match its locked commit')
    if git(reference, 'remote', 'get-url', 'origin') != lock['repository']:
        raise ValueError('reference origin differs from lock')
    if git(reference, 'remote', 'get-url', '--push', 'origin') != 'DISABLED':
        raise ValueError('upstream reference must have pushes disabled')
    if git(reference, 'status', '--porcelain'):
        raise ValueError('reference checkout is dirty')
    return actual


def copy_owned(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise ValueError(f'product payload collides with upstream: {target}')
    if source.is_symlink():
        raise ValueError(f'unsafe product payload: {source}')
    if source.is_dir():
        for directory, subdirs, names in os.walk(source):
            for name in (*subdirs, *names):
                if name in ('__pycache__', '.venv', 'node_modules') or name.endswith(('.pyc', '.pyo')):
                    continue
                path = Path(directory) / name
                if path.is_symlink() or name == '.env' or name.startswith('.env.'):
                    raise ValueError(f'unsafe product payload: {path}')
            subdirs[:] = [name for name in subdirs if name not in ('__pycache__', '.venv', 'node_modules')]
    elif source.name == '.env' or source.name.startswith('.env.'):
        raise ValueError(f'unsafe product payload: {source}')
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(
            '__pycache__', '*.pyc', '*.pyo', '.venv', 'node_modules'))
    else:
        shutil.copy2(source, target)


def guard_python_functions(source: str, path: str, function_names: list[str], statements: list[str]) -> str:
    """Insert a reviewed early guard while retaining the upstream implementation."""
    tree = ast.parse(source)
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    if set(function_names) - set(functions):
        raise ValueError(f'guarded functions moved in {path}: {set(function_names) - set(functions)}')
    lines = source.splitlines(keepends=True)
    for name in sorted(function_names, key=lambda item: functions[item].lineno, reverse=True):
        node = functions[name]
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            insert_at = first.end_lineno
        else:
            insert_at = first.lineno - 1
        indent = ' ' * (node.col_offset + 4)
        lines[insert_at:insert_at] = [indent + statement + '\n' for statement in statements]
    result = ''.join(lines)
    compile(result, path, 'exec')
    return result


def patch_model_picker(source: str, path: str, mode: str) -> str:
    """Apply narrow, anchored picker edits; fail qualification on upstream drift."""
    edits = {
        'show_unconfigured_model_providers': [
            ('            for_picker=True,\n',
             '            for_picker=True, include_unconfigured=True, picker_hints=True,\n'),
            ('        )["providers"]\n    except Exception:\n',
             '        )["providers"]\n        providers = [p for p in providers if p.get("slug") != "nous"]\n    except Exception:\n'),
            ('            provider_data = providers[selected]\n',
             '            provider_data = providers[selected]\n'
             '            if provider_data.get("authenticated") is False:\n'
             '                from agent_eda.provider_setup import start_provider_setup\n'
             '                start_provider_setup(self, provider_data["slug"])\n'
             '                return\n'),
        ],
        'label_unconfigured_model_providers': [
            ("                label = _tn(\"cli.tui.picker_provider_row\", count, name=p['name'])\n",
             "                label = (f\"{p['name']}  · set up\" if p.get('authenticated') is False\n"
             "                         else _tn(\"cli.tui.picker_provider_row\", count, name=p['name']))\n"),
        ],
        'brand_oauth_relogin_command': [
            ('    return f"hermes {profile_cli_selector()}auth add {slug} --type oauth"\n',
             '    return f"eda {profile_cli_selector()}auth add {slug} --type oauth"\n'),
        ],
    }
    for old, new in edits[mode]:
        if source.count(old) != 1:
            raise ValueError(f'picker patch anchor changed in {path}: {old!r}')
        source = source.replace(old, new, 1)
    compile(source, path, 'exec')
    return source


def patch_cli_provider_selection(source: str, path: str) -> str:
    """Remove the disabled Nous provider from the command-line setup menu."""
    edits = (
        ('                print("No provider is set up yet — pick one below. (Nous Portal works without an API key.)")',
         '                print("No provider is set up yet — pick one below.")'),
        ('    provider_idx = _prompt_provider_choice(\n        [label for _, label, _ in ordered],',
         '    default_key = ordered[default_idx][0] if 0 <= default_idx < len(ordered) else None\n'
         '    ordered = [row for row in ordered if row[0] != "nous"]\n'
         '    default_idx = next((i for i, row in enumerate(ordered) if row[0] == default_key), 0)\n'
         '    provider_idx = _prompt_provider_choice(\n        [label for _, label, _ in ordered],'),
    )
    for old, new in edits:
        if source.count(old) != 1:
            raise ValueError(f'provider selection anchor changed in {path}: {old!r}')
        source = source.replace(old, new, 1)
    compile(source, path, 'exec')
    return source


def patch_windows_radio_picker(source: str, path: str) -> str:
    """Use prompt_toolkit's scrolling radio picker where curses is unavailable on Windows."""
    old = ('    """Text-based numbered fallback for radio selection (section titles printed, never numbered)."""\n'
           '    print(color(f"\\n  {title}", Colors.YELLOW))')
    new = ('    """Use an arrow-key picker on Windows; retain the numbered terminal fallback elsewhere."""\n'
           '    if sys.platform == "win32":\n'
           '        from prompt_toolkit.shortcuts import radiolist_dialog\n'
           '        choice = radiolist_dialog(\n'
           '            title=title, text="Up/Down to select, Enter to confirm; Tab to Cancel",\n'
           '            values=[(i, f"{headings[i]} — {radio_item_plain(item)}"\n'
           '                     if headings and i in headings else radio_item_plain(item))\n'
           '                    for i, item in enumerate(items)],\n'
           '            default=selected,\n'
           '        ).run()\n'
           '        if choice is None:\n'
           '            _notify_scoped_navigation(MenuNavigationEvent.CANCEL)\n'
           '            return cancel_returns\n'
           '        return choice\n'
           '    print(color(f"\\n  {title}", Colors.YELLOW))')
    if source.count(old) != 1:
        raise ValueError(f'Windows radio picker anchor changed in {path}')
    source = source.replace(old, new, 1)
    compile(source, path, 'exec')
    return source


def assemble(product: Path, reference: Path, output: Path, selected: str = 'baseline',
             progress: Callable[[str], None] | None = None) -> dict:
    def announce(message: str) -> None:
        if progress is not None:
            progress(message)

    product, reference = product.resolve(), reference.resolve()
    output = output.resolve()
    workspace = product.parent
    if output.parent != workspace / 'builds' or output.exists() or any(p.is_symlink() for p in output.parents):
        raise ValueError('output must be a new immediate child of workspace/builds')
    lock = json.loads((product / 'distribution/upstream.lock.json').read_text(encoding='utf-8'))
    sha = checked_source(reference, lock, selected)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.assemble-', dir=output.parent) as temp:
        staged = Path(temp) / 'runtime'
        staged.mkdir()
        with tempfile.TemporaryFile() as stream:
            announce('Archiving the pinned Hermes source...')
            subprocess.run(['git', '-C', str(reference), 'archive', '--format=tar', sha],
                           stdout=stream, check=True)
            stream.seek(0)
            with tarfile.open(fileobj=stream) as archive:
                members = archive.getmembers()
                total = len(members)
                announce(f'Extracting Hermes source: 0/{total:,} entries')

                def counted_members():
                    for count, member in enumerate(members, 1):
                        yield member
                        if count % 2000 == 0 or count == total:
                            announce(f'Extracting Hermes source: {count:,}/{total:,} entries')

                archive.extractall(staged, members=counted_members(), filter='data')
        # Product code has its own namespace. Installation into native discovery paths
        # is a separate, qualified adapter operation.
        announce('Adding Agent-EDA tools, skills, and CLI adapters...')
        copy_owned(product / 'electronics', staged / 'agent_eda')
        copy_owned(product / 'distribution/branding', staged / 'agent_eda_branding')
        # Standalone tool packages must also live on the native `tools` import
        # path. Each destination is required to be absent from clean upstream.
        for source in sorted((product / 'electronics/tools').iterdir()):
            if source.name == 'agent_eda_plugins':
                continue
            copy_owned(source, staged / 'tools' / source.name)
        # Small, reviewed core adapters carry the existing CLI appearance.
        # Record every upstream file they replace so each Hermes update can
        # detect drift before promoting a build.
        replaced = {}
        overlay_root = product / 'distribution/overlays'
        declarations = json.loads((overlay_root / 'manifest.json').read_text(encoding='utf-8'))['core_adapters']
        declared = {entry['path']: entry for entry in declarations}
        for source in sorted(overlay_root.rglob('*')) if overlay_root.exists() else ():
            if not source.is_file():
                continue
            if '__pycache__' in source.parts or source.suffix in ('.pyc', '.pyo'):
                continue
            if source == overlay_root / 'manifest.json':
                continue
            rel = source.relative_to(overlay_root)
            target = staged / rel
            if not target.is_file() or target.is_symlink():
                raise ValueError(f'core adapter target missing or unsafe: {rel}')
            entry = declared.get(rel.as_posix())
            if not entry:
                raise ValueError(f'core adapter is undeclared or changed: {rel}')
            adapter = checked_adapter_bytes(source, entry, rel.as_posix())
            if entry['upstream_sha256'][selected] != digest(target):
                raise ValueError(f'upstream changed under core adapter: {rel}')
            replaced[rel.as_posix()] = {'upstream_sha256': digest(target),
                                       'product_sha256': hashlib.sha256(adapter).hexdigest()}
            target.write_bytes(adapter)
        if set(replaced) != set(declared):
            raise ValueError(f'core adapter declarations differ from sources: {set(declared) ^ set(replaced)}')
        guards = {
            'disable_nous_login': ['raise SystemExit("Nous Portal login is disabled in Agent-EDA; select another provider with `eda model`.")'],
            'route_slash_update': ['self._pending_relaunch = ["update"]', 'return True'],
            'disable_native_update': ['raise SystemExit("Agent-EDA updates require a qualified pinned build; run `eda --check-upstream` to inspect releases.")'],
            'route_product_update': [
                'if list(extra_args) == ["update"] and os.environ.get("AGENT_EDA_PRODUCT"):',
                '    launcher = pathlib.Path(os.environ["AGENT_EDA_PRODUCT"]) / "bin" / "eda"',
                '    if sys.platform == "win32":',
                '        import subprocess',
                '        raise SystemExit(subprocess.call([sys.executable, str(launcher), "update"]))',
                '    os.execv(sys.executable, [sys.executable, str(launcher), "update"])',
                '    return',
            ],
        }
        patches = json.loads((product / 'distribution/patches/manifest.json').read_text(encoding='utf-8'))['patches']
        for entry in patches:
            target = staged / entry['path']
            if digest(target) != entry['upstream_sha256'][selected]:
                raise ValueError(f'upstream changed under guarded patch: {entry["path"]}')
            if entry['guard'] in guards:
                patched = guard_python_functions(target.read_text(encoding='utf-8'), entry['path'],
                                                 entry['functions'], guards[entry['guard']])
                if entry['guard'] == 'disable_native_update':
                    patched = patch_cli_provider_selection(patched, entry['path'])
            else:
                if entry['guard'] == 'windows_radio_picker':
                    patched = patch_windows_radio_picker(target.read_text(encoding='utf-8'), entry['path'])
                else:
                    patched = patch_model_picker(target.read_text(encoding='utf-8'), entry['path'], entry['guard'])
            target.write_text(patched, encoding='utf-8')
            replaced[entry['path']] = {
                'upstream_sha256': entry['upstream_sha256'][selected],
                'product_sha256': digest(target), 'patch': entry['guard']}
        announce('Verifying assembled source file hashes...')
        files = {}
        for path in sorted(staged.rglob('*')):
            if not path.is_file():
                continue
            files[path.relative_to(staged).as_posix()] = digest(path)
            if len(files) % 2000 == 0:
                announce(f'Verified {len(files):,} source files...')
        announce(f'Verified {len(files):,} source files.')
        manifest = {'phase': 'assembled-source-only', 'qualified': False,
                    'upstream': {'repository': lock['repository'], **lock[selected]},
                    'product': json.loads((product / 'distribution/product.json').read_text(encoding='utf-8')),
                    'core_adapters': replaced,
                    'source_files_sha256': files}
        (staged / '.agent-eda-assembly.json').write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        if output.exists():
            raise FileExistsError(output)
        os.replace(staged, output)
    return {'output': str(output), 'selected': selected, 'upstream_commit': sha,
            'source_files': len(files), 'qualified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--selected', choices=('baseline', 'candidate'), default='baseline')
    opts = parser.parse_args()
    print(json.dumps(assemble(opts.product, opts.reference, opts.output, opts.selected), indent=2))
