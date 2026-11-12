"""Probe installed distributions and package assets from an unrelated directory."""
import argparse
import importlib
import importlib.metadata
import importlib.resources
import json
import os
from pathlib import Path
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--component', choices=['core', 'shared', 'home', 'french'], required=True)
args = parser.parse_args()
modules = {'core': 'bot_center', 'shared': 'bot_center_runtime', 'home': 'home_bot', 'french': 'french_notes'}
original_directory = Path.cwd()
with tempfile.TemporaryDirectory(prefix='bot-center-installed-') as directory:
    try:
        os.chdir(directory)
        module = importlib.import_module(modules[args.component])
        location = Path(module.__file__).resolve()
        if 'site-packages' not in str(location).casefold():
            raise RuntimeError('Production probe imported source outside installed site-packages.')
        if args.component != 'shared':
            shared = importlib.import_module('bot_center_runtime')
            if 'site-packages' not in str(Path(shared.__file__).resolve()).casefold():
                raise RuntimeError('Shared runtime consumer is not installed from an artifact.')
        root = importlib.resources.files(modules[args.component])
        if args.component == 'home' and not (root / 'seed_tasks.json').is_file():
            raise RuntimeError('Home seed assets were not packaged.')
        if args.component == 'french' and not (root / 'ui/editor.html').is_file():
            raise RuntimeError('French editor UI assets were not packaged.')
        print(json.dumps({'status': 'Passed', 'component': args.component, 'module': str(location), 'unrelated_cwd': True}))
    finally:
        # Windows cannot remove the process's current working directory.
        os.chdir(original_directory)
