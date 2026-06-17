# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-спецификация сборки десктоп-приложения Re.form CRM.
# Сборка:  pyinstaller reform.spec --noconfirm   (результат в dist/ReformCRM/)

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []

# pywebview тащит платформенные бэкенды и ресурсы — собираем целиком.
for pkg in ("webview",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# aiogram использует динамические импорты — подхватим все подмодули.
hiddenimports += collect_submodules("aiogram")
# pythonnet (clr) нужен pywebview на Windows (WebView2 / WinForms).
hiddenimports += ["clr"]

a = Analysis(
    ["app_desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ReformCRM",
    console=False,        # оконное приложение, без чёрной консоли
    icon=None,            # положи visual/assets/icon.ico и укажи путь сюда
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="ReformCRM",
)
