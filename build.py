"""Builds Taipan Studio into a single exe: dist/Taipan Studio.exe

    pip install -r requirements.txt
    python build.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, 'app')


def run(*cmd):
    print('>', ' '.join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)


def main():
    run(sys.executable, os.path.join('tests', 'test_m810.py'))
    run(sys.executable, os.path.join('tests', 'test_combos.py'))
    run(sys.executable, os.path.join('tests', 'test_save.py'))
    if not os.path.exists(os.path.join(APP, 'assets', 'icon.ico')):
        run(sys.executable, os.path.join('tools', 'make_icon.py'))
    sep = os.pathsep
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'Taipan Studio', '--icon', os.path.join(APP, 'assets', 'icon.ico'),
        '--add-data', os.path.join(APP, 'assets') + sep + 'assets',
        '--add-data', os.path.join(APP, 'ui') + sep + 'ui',
        '--collect-all', 'webview', '--exclude-module', 'tkinter',
        '--distpath', os.path.join(ROOT, 'dist'), '--workpath', os.path.join(ROOT, 'build'),
        '--specpath', os.path.join(ROOT, 'build'),
        os.path.join(APP, 'taipan.py'))
    print('\nBuilt dist/Taipan Studio.exe')


if __name__ == '__main__':
    main()
