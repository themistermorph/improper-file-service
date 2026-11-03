"""Statische Prüfungen: Menüs hängen an Berechtigungen, nicht am Adminflag."""

from __future__ import annotations

import re
from pathlib import Path

HTML_PATH = Path(__file__).resolve().parents[1] / "src/ifs/web/index.html"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _function_block(source: str, name: str) -> str:
    match = re.search(rf"\n    (?:async )?function {re.escape(name)}\s*\(", source)
    assert match, f"function {name} nicht gefunden"
    rest = source[match.end():]
    nxt = re.search(r"\n    (?:async )?function \w+\s*\(", rest)
    end = match.end() + nxt.start() if nxt else len(source)
    return source[match.start():end]


def test_account_menue_nutzt_systemrechte():
    block = _function_block(_html(), "applyAccountMenus")
    assert 'systemPerms.has("monitor")' in block
    assert 'systemPerms.has("audit")' in block
    assert "info.is_admin" in block
    # Benutzerverwaltung bleibt Admin vorbehalten.
    assert "menu-users" in block
    start = _function_block(_html(), "start")
    assert "applyAccountMenus(me)" in start


def test_rollen_menue_ist_entfernt():
    source = _html()
    assert 'id="menu-roles"' not in source
    assert "showView('roles')" not in source


def test_zeilenmenue_nutzt_entry_permissions():
    block = _function_block(_html(), "render")
    assert "new Set(e.permissions || [])" in block
    for permission in ("publish", "share", "admin", "write", "delete"):
        assert f'can("{permission}")' in block, f"Menü prüft {permission} nicht"
    # „Zugriff“ hängt direkt an can("admin"), nicht am Systemadminflag.
    access = block.index('label: "Zugriff"')
    assert 'if (can("admin"))' in block[max(0, access - 100) : access]


def test_logout_setzt_alle_menues_zurueck():
    block = _function_block(_html(), "logout")
    for element_id in ("menu-users", "menu-system", "menu-audit"):
        assert element_id in block


def test_zugriff_dialog_nutzt_acl_statt_rollen():
    block = _function_block(_html(), "entryAccessDialog")
    assert '"/api/entries/" + entry.id + "/acl"' in block
    assert '"/api/principals" + query' in _function_block(_html(), "loadAccessPrincipals")
    assert "/api/roles" not in block
    assert "/api/users" not in block
    assert "/api/groups" not in block
    assert "entryPermCheckboxes" in block

    submit = _function_block(_html(), "submitEntryAccess")
    assert 'apiJson("PUT", "/api/entries/" + accessEntry.id + "/acl"' in submit
    assert "refreshMe()" in submit

    remove = _function_block(_html(), "removeEntryAcl")
    assert 'api("DELETE", "/api/entries/" + accessEntry.id + "/acl/" + aclId)' in remove


def test_zugriff_dialog_hat_alle_eintragsrechte():
    source = _html()
    assert "const ENTRY_PERMS = [" in source
    for permission in ("read", "write", "delete", "share", "publish", "admin"):
        assert f'"{permission}"' in source


def test_benutzerdialog_verwaltet_audit_und_monitor():
    edit = _function_block(_html(), "userEditDialog")
    assert '"/api/users/" + u.id + "/permissions"' in edit
    assert 'id="eu-audit"' in edit
    assert 'id="eu-monitor"' in edit

    submit = _function_block(_html(), "submitUserEdit")
    assert 'apiJson("PUT", "/api/users/" + id + "/permissions"' in submit
    assert 'systemPerms.push("audit")' in submit
    assert 'systemPerms.push("monitor")' in submit
    assert "refreshMe()" in submit


def test_geteilt_ansicht_zeigt_ausgehende_acls():
    source = _html()
    assert 'id="nav-shared-out"' in source
    assert "showView('shared-out')" in source
    assert 'id="shared-out-rows"' in source

    load = _function_block(source, "loadSharedOut")
    assert '"/api/shared/outgoing"' in load

    render = _function_block(source, "renderSharedOut")
    assert "entryAccessDialog" in render
    assert "ENTRY_PERM_LABELS" in render

    show = _function_block(source, "showView")
    assert 'view === "shared-out"' in show


def test_geteilt_menue_verwaltet_bestehende_links():
    render = _function_block(_html(), "render")
    label = render.index('label: "Geteilt"')
    guard = render.index('if (can("share"))', max(0, label - 200))
    assert 0 < label - guard < 200

    dialog = _function_block(_html(), "entryShareDialog")
    assert '"/api/shares?entry_id=" + entry.id' in dialog
    assert "shareDialog(entryShareEntry)" in dialog
    assert "copyEntryShare" in dialog
    assert "editEntryShare" in dialog
    assert "revokeEntryShare" in dialog

    for function in ("copyEntryShare", "editEntryShare", "revokeEntryShare"):
        assert _function_block(_html(), function)

