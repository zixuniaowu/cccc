#!/usr/bin/env python3
"""
CCCC PTK TUI - Interactive Orchestrator Interface

A modern CLI interface for dual-agent collaboration with CCCC.

Features:
- Setup phase: Quick actor configuration (compact layout)
- Runtime phase: Interactive CLI with Timeline, Input, Status
- Commands: /a, /b, /both, /help, /status, /queue, /locks
- Command auto-completion with descriptions
- Command history with up/down navigation (1000 commands)
- Ctrl+R reverse search through history
- Standard editing shortcuts (Ctrl+A/E/W/U/K)
- Clear screen (Ctrl+L)
- Timeline navigation (PageUp/PageDown)
- Timestamped messages with type indicators
- Input validation with helpful error messages
- Real-time updates from outbox.jsonl
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from prompt_toolkit import Application
from prompt_toolkit.application import get_app
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    Layout, HSplit, VSplit, Window, Float, FloatContainer,
    FormattedTextControl, Dimension
)
from prompt_toolkit.widgets import (
    TextArea, Button, Dialog, RadioList, Label
)
from prompt_toolkit.styles import Style


class CommandCompleter(Completer):
    """Auto-completion for CCCC commands"""

    def __init__(self):
        # (command, description, example)
        self.commands = [
            ('/a', 'Send message to PeerA only', '/a <message>'),
            ('/b', 'Send message to PeerB only', '/b <message>'),
            ('/both', 'Send message to both peers', '/both <message>'),
            ('/status', 'Show orchestrator status', '/status'),
            ('/queue', 'Show commit queue', '/queue'),
            ('/locks', 'Show path locks', '/locks'),
            ('/help', 'Show available commands', '/help'),
        ]

    def get_completions(self, document: Document, complete_event):
        """Generate completions based on current input"""
        text = document.text_before_cursor

        # Only complete if starting with /
        if not text or not text.startswith('/'):
            return

        # Get the command part (before any space)
        parts = text.split(' ', 1)
        cmd_prefix = parts[0].lower()

        # If already typed space, no more command completion
        if len(parts) > 1:
            return

        # Find matching commands
        for cmd, desc, example in self.commands:
            if cmd.startswith(cmd_prefix):
                # Calculate how many chars to replace
                start_pos = -len(cmd_prefix)
                # Format: "/cmd     Description (usage)"
                display_text = f'{cmd:<8} {desc}'
                yield Completion(
                    text=cmd + ' ',  # Add space after completion
                    start_position=start_pos,
                    display=display_text,
                    style='class:completion'
                )


@dataclass
class SetupConfig:
    """Configuration state"""
    peerA: str = ''
    peerB: str = ''
    aux: str = 'none'
    foreman: str = 'none'
    mode: str = 'tmux'
    tg_token: str = ''
    tg_chat: str = ''
    sl_token: str = ''
    sl_chan: str = ''
    dc_token: str = ''
    dc_chan: str = ''

    def is_valid(self, actors: List[str], home: Path) -> tuple[bool, str]:
        """Validate configuration"""
        if not self.peerA or self.peerA == '(unset)':
            return False, "PeerA actor required"
        if not self.peerB or self.peerB == '(unset)':
            return False, "PeerB actor required"

        # Check CLI availability
        missing = []
        for role, actor in [('PeerA', self.peerA), ('PeerB', self.peerB)]:
            if actor and actor != 'none':
                cmd = _get_actor_command(home, actor)
                if cmd and not shutil.which(cmd.split()[0]):
                    missing.append(f"{role}→{actor}")

        if missing:
            return False, f"CLI not on PATH: {', '.join(missing)}"

        if self.mode == 'telegram' and not self.tg_token:
            return False, "Telegram token required"
        if self.mode == 'slack' and not self.sl_token:
            return False, "Slack token required"
        if self.mode == 'discord' and not self.dc_token:
            return False, "Discord token required"

        return True, ""


def _get_actor_command(home: Path, actor: str) -> Optional[str]:
    """Get CLI command for actor"""
    try:
        import yaml
        p = home / "settings" / "agents.yaml"
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
            acts = data.get('actors') or {}
            peer = (acts.get(actor) or {}).get('peer') or {}
            return str(peer.get('command') or '')
    except Exception:
        pass
    return None


def _write_yaml(home: Path, rel_path: str, data: Dict[str, Any]) -> None:
    """Write YAML file"""
    try:
        import yaml
        p = home / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')
    except Exception:
        p = home / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def create_header() -> Window:
    """Flexible header with welcome message"""
    text = [
        ('class:title', '═══ CCCC Setup - Dual-Agent Orchestrator ═══\n'),
        ('class:hint', 'Two AI agents collaborate as equals. Configure your actors below.\n'),
        ('class:hint', 'Tab: Next · Enter: Select · Esc: Cancel'),
    ]
    return Window(
        content=FormattedTextControl(text),
        height=Dimension(min=3, max=4),
        dont_extend_height=True
    )


def create_runtime_header() -> Window:
    """Flexible runtime header"""
    text = [
        ('class:title', '═══ CCCC Orchestrator (Interactive CLI) ═══\n'),
        ('class:hint', 'Commands: /a /b /both /help /status  ·  Exit: Ctrl+b d (detach tmux)'),
    ]
    return Window(
        content=FormattedTextControl(text),
        height=Dimension(min=2, max=3),
        dont_extend_height=True
    )


def create_section_header(title: str) -> Window:
    """Section separator"""
    text = [('class:section', f'─── {title} ' + '─' * (40 - len(title)))]
    return Window(content=FormattedTextControl(text), height=1, dont_extend_height=True)


class CCCCSetupApp:
    """Main TUI application"""

    def __init__(self, home: Path):
        self.home = home
        self.config = SetupConfig()
        self.actors_available: List[str] = []
        self.error_msg: str = ''
        self.setup_visible: bool = True
        self.modal_open: bool = False
        self.current_dialog: Optional[Float] = None
        self.dialog_ok_handler: Optional[callable] = None

        # Command history
        self.command_history: List[str] = []
        self.history_index: int = -1
        self.current_input: str = ''

        # Reverse search state
        self.reverse_search_mode: bool = False
        self.search_query: str = ''
        self.search_results: List[str] = []
        self.search_index: int = 0

        # Load config
        self._load_existing_config()

        # Build UI
        self.error_label = Label(text='', style='class:error')
        self.buttons: List[Button] = []
        self.setup_content = self._build_setup_panel()

        # Runtime UI
        # Initial welcome message with timestamp and new format
        initial_msg = f"{time.strftime('%H:%M:%S')} ┃SYS┃ CCCC Orchestrator ready. Type /help for available commands and shortcuts.\n"
        self.timeline = TextArea(
            text=initial_msg,
            scrollbar=True,
            read_only=True,
            focusable=False
        )
        self.input_field = TextArea(
            height=1,
            prompt='> ',
            multiline=False,
            completer=CommandCompleter(),
            complete_while_typing=True
        )
        self.status = TextArea(
            height=Dimension(min=3, max=8, preferred=5),
            read_only=True,
            focusable=False
        )

        # Layout
        self.floats: List[Float] = []
        self.root = FloatContainer(
            content=HSplit([
                create_header(),
                Window(height=1),
                self.setup_content,
            ]),
            floats=self.floats
        )

        # Key bindings
        self.kb = self._create_key_bindings()

        # Application with flexible sizing
        self.app = Application(
            layout=Layout(self.root, min_available_height=10),  # Minimum 10 lines
            key_bindings=self.kb,
            mouse_support=True,
            full_screen=True,
            style=Style.from_dict({
                'title': '#00aaff bold',
                'section': '#888888',
                'label': '#cccccc',
                'indicator': '#00ff00',
                'value': '#ffffff',
                'hint': '#666666',
                'error': '#ff0000 bold',
                'dialog': 'bg:#111111',
                'dialog.body': 'bg:#222222',
                'button': 'bg:#333333 #ffffff',
                'button.focused': 'bg:#0077ff #ffffff bold',
                # Completion menu styles
                'completion-menu': 'bg:#222222 #cccccc',
                'completion-menu.completion': 'bg:#222222 #cccccc',
                'completion-menu.completion.current': 'bg:#0077ff #ffffff bold',
                'completion-menu.meta': 'bg:#222222 #888888',
                'completion-menu.meta.current': 'bg:#0077ff #cccccc',
            })
        )

    def _load_existing_config(self) -> None:
        """Load configuration"""
        # Load actors
        try:
            import yaml
            p = self.home / "settings" / "agents.yaml"
            if p.exists():
                data = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
                acts = data.get('actors') or {}
                self.actors_available = list(acts.keys()) if isinstance(acts, dict) else []
        except Exception:
            pass

        if not self.actors_available:
            self.actors_available = ['claude', 'codex', 'gemini', 'droid', 'opencode']

        # Load roles
        try:
            import yaml
            p = self.home / "settings" / "cli_profiles.yaml"
            if p.exists():
                data = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
                roles = data.get('roles') or {}
                self.config.peerA = str((roles.get('peerA') or {}).get('actor') or '')
                self.config.peerB = str((roles.get('peerB') or {}).get('actor') or '')
                self.config.aux = str((roles.get('aux') or {}).get('actor') or 'none')
        except Exception:
            pass

        # Smart defaults
        if not self.config.peerA and len(self.actors_available) > 0:
            self.config.peerA = self.actors_available[0]
        if not self.config.peerB and len(self.actors_available) > 1:
            self.config.peerB = self.actors_available[1]
        elif not self.config.peerB and len(self.actors_available) > 0:
            self.config.peerB = self.actors_available[0]

        # Load foreman
        try:
            import yaml
            p = self.home / "settings" / "foreman.yaml"
            if p.exists():
                data = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
                self.config.foreman = str(data.get('agent') or 'none')
        except Exception:
            pass

        # Load telegram
        try:
            import yaml
            p = self.home / "settings" / "telegram.yaml"
            if p.exists():
                data = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
                self.config.tg_token = str(data.get('token') or '')
                chats = data.get('allow_chats') or []
                if isinstance(chats, list) and chats:
                    self.config.tg_chat = str(chats[0])
                if data.get('autostart') and self.config.tg_token:
                    self.config.mode = 'telegram'
        except Exception:
            pass

    def _build_setup_panel(self) -> HSplit:
        """Build compact setup panel (8-char labels)"""
        # Buttons
        btn_peerA = Button(
            text=self._format_button_text(self.config.peerA, required=True),
            handler=lambda: self._show_actor_dialog('peerA')
        )
        btn_peerB = Button(
            text=self._format_button_text(self.config.peerB, required=True),
            handler=lambda: self._show_actor_dialog('peerB')
        )
        btn_aux = Button(
            text=self._format_button_text(self.config.aux, none_ok=True),
            handler=lambda: self._show_actor_dialog('aux')
        )
        btn_foreman = Button(
            text=self._format_button_text(self.config.foreman, none_ok=True),
            handler=self._show_foreman_dialog
        )
        btn_mode = Button(
            text=f'[●] {self.config.mode}',
            handler=self._show_mode_dialog
        )
        btn_confirm = Button(
            text='Launch',
            handler=self._confirm_and_launch
        )

        self.btn_peerA = btn_peerA
        self.btn_peerB = btn_peerB
        self.btn_aux = btn_aux
        self.btn_foreman = btn_foreman
        self.btn_mode = btn_mode
        self.btn_confirm = btn_confirm
        self.buttons = [btn_peerA, btn_peerB, btn_aux, btn_foreman, btn_mode]

        # Compact labels with helpful descriptions
        items: List[Any] = [
            self.error_label,
            Window(height=1),
            create_section_header('Core Collaboration'),
            Label(text='  Two equal peers collaborate via mailbox contract', style='class:hint'),
            Window(height=1),
            VSplit([Window(width=10, content=FormattedTextControl('PeerA:')), btn_peerA], padding=1),
            Label(text='    Primary collaborative agent (plans, reviews, iterates)', style='class:hint'),
            VSplit([Window(width=10, content=FormattedTextControl('PeerB:')), btn_peerB], padding=1),
            Label(text='    Secondary collaborative agent (implements, tests, verifies)', style='class:hint'),
            Window(height=1),
            create_section_header('Optional Agents'),
            Label(text='  Additional actors (set to "none" to disable)', style='class:hint'),
            Window(height=1),
            VSplit([Window(width=10, content=FormattedTextControl('Aux:')), btn_aux], padding=1),
            Label(text='    On-demand helper for burst work (reviews, tests, bulk ops)', style='class:hint'),
            VSplit([Window(width=10, content=FormattedTextControl('Foreman:')), btn_foreman], padding=1),
            Label(text='    Periodic user proxy (runs tasks on timer, optional)', style='class:hint'),
            Window(height=1),
            create_section_header('Interaction Mode'),
            Label(text='  How to communicate with orchestrator', style='class:hint'),
            Window(height=1),
            VSplit([Window(width=10, content=FormattedTextControl('Mode:')), btn_mode], padding=1),
            Label(text='    tmux: local only | telegram/slack/discord: team chat', style='class:hint'),
        ]

        # Provider button if IM mode
        if self.config.mode in ('telegram', 'slack', 'discord'):
            btn_provider = Button(
                text=self._get_provider_summary(),
                handler=self._show_provider_dialog
            )
            self.btn_provider = btn_provider
            self.buttons.append(btn_provider)
            items.extend([
                Window(height=1),
                VSplit([Window(width=10, content=FormattedTextControl('Provider:')), btn_provider], padding=1),
            ])

        items.extend([
            Window(height=1),
            Label(text='─' * 40, style='class:section'),  # Flexible separator
            Window(height=1),
            btn_confirm,
        ])

        return HSplit(items)

    def _format_button_text(self, value: str, required: bool = False, none_ok: bool = False) -> str:
        """Format button text"""
        if not value or value == '(unset)':
            return '[○] (unset)' if required else '[○] none'
        if value == 'none' and none_ok:
            return '[○] none'
        return f'[●] {value}'

    def _get_provider_summary(self) -> str:
        """Provider summary"""
        mode = self.config.mode
        if mode == 'telegram':
            tok = '●' if self.config.tg_token else '○'
            return f'[{tok}] token set' if self.config.tg_token else '[○] not configured'
        elif mode == 'slack':
            tok = '●' if self.config.sl_token else '○'
            return f'[{tok}] token set' if self.config.sl_token else '[○] not configured'
        elif mode == 'discord':
            tok = '●' if self.config.dc_token else '○'
            return f'[{tok}] token set' if self.config.dc_token else '[○] not configured'
        return 'Configure...'

    def _refresh_ui(self) -> None:
        """Refresh UI"""
        self.btn_peerA.text = self._format_button_text(self.config.peerA, required=True)
        self.btn_peerB.text = self._format_button_text(self.config.peerB, required=True)
        self.btn_aux.text = self._format_button_text(self.config.aux, none_ok=True)
        self.btn_foreman.text = self._format_button_text(self.config.foreman, none_ok=True)
        self.btn_mode.text = f'[●] {self.config.mode}'

        if hasattr(self, 'btn_provider'):
            self.btn_provider.text = self._get_provider_summary()

        if self.error_msg:
            self.error_label.text = f'⚠️  {self.error_msg}'
        else:
            self.error_label.text = ''

        try:
            self.app.invalidate()
        except Exception:
            pass

    def _show_actor_dialog(self, role: str) -> None:
        """Show actor dialog with Enter/Esc bindings"""
        if self.modal_open:
            return

        choices = [(a, a) for a in self.actors_available]
        if role == 'aux':
            choices.insert(0, ('none', 'none'))

        def on_ok() -> None:
            setattr(self.config, role, radio.current_value)
            self._close_dialog()
            self._refresh_ui()

        # RadioList with accept_handler for Enter key
        radio = RadioList(choices, accept_handler=on_ok)
        current = getattr(self.config, role, '')
        if current and current in [c[0] for c in choices]:
            radio.current_value = current

        # Create keybindings for Esc
        kb_body = KeyBindings()

        @kb_body.add('escape')
        def _escape(event):
            self._close_dialog()

        dialog = Dialog(
            title=f'Select {role.upper()}',
            body=HSplit([
                Label(text=f'Choose actor for {role}:'),
                Window(height=1),
                radio,
            ], key_bindings=kb_body),
            buttons=[
                Button('OK (Enter)', handler=on_ok),
                Button('Cancel (Esc)', handler=self._close_dialog),
            ],
            width=50,
            modal=True
        )

        self._open_dialog(dialog, on_ok)
        try:
            self.app.layout.focus(radio)
        except Exception:
            pass

    def _show_foreman_dialog(self) -> None:
        """Show foreman dialog"""
        if self.modal_open:
            return

        choices = [('none', 'none'), ('reuse_aux', "reuse aux's agent")]
        choices.extend([(a, a) for a in self.actors_available])

        def on_ok() -> None:
            self.config.foreman = radio.current_value
            self._close_dialog()
            self._refresh_ui()

        # RadioList with accept_handler for Enter key
        radio = RadioList(choices, accept_handler=on_ok)
        if self.config.foreman in [c[0] for c in choices]:
            radio.current_value = self.config.foreman

        # Create keybindings for Esc
        kb_body = KeyBindings()

        @kb_body.add('escape')
        def _escape(event):
            self._close_dialog()

        dialog = Dialog(
            title='Foreman Agent',
            body=HSplit([
                Label(text='Select foreman agent:'),
                Window(height=1),
                radio,
            ], key_bindings=kb_body),
            buttons=[
                Button('OK (Enter)', handler=on_ok),
                Button('Cancel (Esc)', handler=self._close_dialog)
            ],
            width=50,
            modal=True
        )

        self._open_dialog(dialog, on_ok)
        try:
            self.app.layout.focus(radio)
        except Exception:
            pass

    def _show_mode_dialog(self) -> None:
        """Show mode dialog"""
        if self.modal_open:
            return

        choices = [
            ('tmux', 'tmux only'),
            ('telegram', 'Telegram'),
            ('slack', 'Slack'),
            ('discord', 'Discord'),
        ]

        def on_ok() -> None:
            old_mode = self.config.mode
            self.config.mode = radio.current_value
            self._close_dialog()

            # Rebuild if mode changed
            if old_mode != self.config.mode:
                self.setup_content = self._build_setup_panel()
                self.root.content = HSplit([
                    create_header(),
                    Window(height=1),
                    self.setup_content,
                ])
                try:
                    self.app.layout.focus(self.btn_peerA)
                except Exception:
                    pass

            self._refresh_ui()

        # RadioList with accept_handler for Enter key
        radio = RadioList(choices, accept_handler=on_ok)
        radio.current_value = self.config.mode

        # Create keybindings for Esc
        kb_body = KeyBindings()

        @kb_body.add('escape')
        def _escape(event):
            self._close_dialog()

        dialog = Dialog(
            title='Interaction Mode',
            body=HSplit([
                Label(text='How to interact?'),
                Window(height=1),
                radio,
            ], key_bindings=kb_body),
            buttons=[
                Button('OK (Enter)', handler=on_ok),
                Button('Cancel (Esc)', handler=self._close_dialog)
            ],
            width=50,
            modal=True
        )

        self._open_dialog(dialog, on_ok)
        try:
            self.app.layout.focus(radio)
        except Exception:
            pass

    def _show_provider_dialog(self) -> None:
        """Show provider dialog"""
        if self.modal_open:
            return

        mode = self.config.mode
        if mode == 'telegram':
            token_field = TextArea(height=1, multiline=False, text=self.config.tg_token)
            chat_field = TextArea(height=1, multiline=False, text=self.config.tg_chat)

            def on_ok() -> None:
                self.config.tg_token = token_field.text.strip()
                self.config.tg_chat = chat_field.text.strip()
                self._close_dialog()
                self._refresh_ui()

            # Create keybindings for the dialog body
            kb_body = KeyBindings()

            @kb_body.add('escape')
            def _escape(event):
                self._close_dialog()

            # Note: Enter in TextArea is for input, use buttons to save
            dialog = Dialog(
                title='Telegram',
                body=HSplit([
                    Label(text='Bot Token:'),
                    token_field,
                    Window(height=1),
                    Label(text='Chat ID (optional):'),
                    chat_field,
                ], key_bindings=kb_body),
                buttons=[
                    Button('Save (Enter)', handler=on_ok),
                    Button('Cancel (Esc)', handler=self._close_dialog)
                ],
                width=60,
                modal=True
            )

            self._open_dialog(dialog, on_ok)
            try:
                self.app.layout.focus(token_field)
            except Exception:
                pass

    def _confirm_and_launch(self) -> None:
        """Validate and launch orchestrator"""
        # Clear error
        self.error_msg = ''
        self._refresh_ui()

        # Validate
        valid, error = self.config.is_valid(self.actors_available, self.home)
        if not valid:
            self.error_msg = error
            self._refresh_ui()
            return

        # Transition to runtime UI first
        self.setup_visible = False
        self._build_runtime_ui()

        # Write initial timeline message
        self._write_timeline("Configuration saved. Orchestrator starting...", 'system')
        self._write_timeline(f"PeerA: {self.config.peerA}, PeerB: {self.config.peerB}", 'system')

        # Save config and write commands
        self._save_config()

        self._write_timeline("Waiting for orchestrator to launch peers...", 'system')
        self._write_timeline("Type /help for available commands.", 'info')

    def _build_runtime_ui(self) -> None:
        """Build flexible runtime UI (Timeline + Input + Status)"""
        # Update status
        self._update_status()

        # Rebuild root with flexible layout
        self.root.content = HSplit([
            create_runtime_header(),
            Window(height=1),
            VSplit([
                # Timeline takes remaining space
                HSplit([
                    Label(text='Timeline:', style='class:section'),
                    self.timeline,
                ], padding=0),
                # Separator
                Window(width=1),
                # Status panel with flexible width (min 20, max 30)
                HSplit([
                    Label(text='Status:', style='class:section'),
                    self.status,
                ], padding=0, width=Dimension(min=20, max=30, preferred=25)),
            ]),
            Window(height=1),
            Label(text='Input (type /help):', style='class:section'),
            self.input_field,
        ])

        try:
            self.app.layout.focus(self.input_field)
        except Exception:
            pass

    def _save_config(self) -> None:
        """Save configuration and trigger orchestrator launch"""
        cmds = self.home / "state" / "commands.jsonl"
        cmds.parent.mkdir(parents=True, exist_ok=True)

        # Write debug timeline entry
        self._write_timeline(f"Writing commands to {cmds}", 'debug')

        with cmds.open('a', encoding='utf-8') as f:
            ts = time.time()

            # Roles
            for role in ['peerA', 'peerB', 'aux']:
                actor = getattr(self.config, role, '')
                if actor and actor != 'none' and actor != '(unset)':
                    cmd = {
                        "type": "roles-set-actor",
                        "args": {"role": role, "actor": actor},
                        "source": "tui",
                        "ts": ts
                    }
                    f.write(json.dumps(cmd, ensure_ascii=False) + '\n')
                    self._write_timeline(f"Set {role} → {actor}", 'debug')

            # IM token
            if self.config.mode == 'telegram' and self.config.tg_token:
                cmd = {
                    "type": "token",
                    "args": {"action": "set", "value": self.config.tg_token},
                    "source": "tui",
                    "ts": ts
                }
                f.write(json.dumps(cmd, ensure_ascii=False) + '\n')
                self._write_timeline(f"Set Telegram token", 'debug')

            # Launch command (triggers orchestrator to start peers)
            cmd = {"type": "launch", "args": {"who": "both"}, "source": "tui", "ts": ts}
            f.write(json.dumps(cmd, ensure_ascii=False) + '\n')
            self._write_timeline(f"Launch command written: {cmd}", 'debug')

        # Foreman yaml
        if self.config.foreman and self.config.foreman != 'none':
            _write_yaml(self.home, 'settings/foreman.yaml', {
                'agent': self.config.foreman,
                'enabled': True
            })

        # IM provider yamls
        if self.config.mode == 'telegram' and self.config.tg_token:
            cfg = {'token': self.config.tg_token, 'token_env': 'TELEGRAM_BOT_TOKEN', 'autostart': True}
            if self.config.tg_chat:
                try:
                    cfg['allow_chats'] = [int(self.config.tg_chat)]
                except ValueError:
                    cfg['allow_chats'] = [self.config.tg_chat]
            _write_yaml(self.home, 'settings/telegram.yaml', cfg)

        # Confirmation flag
        (self.home / "state" / "settings.confirmed").write_text(str(int(ts)))

    def _write_timeline(self, text: str, msg_type: str = 'info') -> None:
        """Append message to timeline with timestamp and styling"""
        # Add timestamp
        timestamp = time.strftime('%H:%M:%S')

        # Add type indicator with unicode box characters for better visual distinction
        type_indicators = {
            'system': '┃SYS┃',
            'peerA': '┃A→U┃',
            'peerB': '┃B→U┃',
            'user': '┃YOU┃',
            'error': '┃ERR┃',
            'debug': '┃DBG┃',
            'info': '┃···┃'
        }
        indicator = type_indicators.get(msg_type, '┃···┃')

        # Format message with better visual separation
        formatted = f'{timestamp} {indicator} {text}'

        # Append to timeline
        current = self.timeline.text
        self.timeline.text = current + formatted + '\n'

        # Auto-scroll to bottom
        self.timeline.buffer.cursor_position = len(self.timeline.text)

    def _start_reverse_search(self) -> None:
        """Start reverse search mode"""
        self.reverse_search_mode = True
        self.search_query = ''
        self.search_results = []
        self.search_index = 0
        self._update_search_prompt()

    def _update_search_prompt(self) -> None:
        """Update input prompt for reverse search"""
        if self.reverse_search_mode:
            if self.search_results:
                result = self.search_results[self.search_index]
                self.input_field.text = result
                # Show search status at bottom of timeline
                status = f"(reverse-i-search)`{self.search_query}': {result}"
                # Update a temporary status line (we'll show it in the prompt)
            else:
                self.input_field.text = ''

    def _perform_reverse_search(self, query: str) -> None:
        """Perform reverse search through command history"""
        self.search_query = query
        self.search_results = []
        self.search_index = 0

        if not query:
            self._update_search_prompt()
            return

        # Search through history in reverse order
        query_lower = query.lower()
        for cmd in reversed(self.command_history):
            if query_lower in cmd.lower():
                self.search_results.append(cmd)

        self._update_search_prompt()

    def _exit_reverse_search(self, accept: bool = False) -> None:
        """Exit reverse search mode"""
        if accept and self.search_results and self.search_index < len(self.search_results):
            # Keep the selected command in input
            self.input_field.text = self.search_results[self.search_index]
        else:
            # Restore original input or clear
            self.input_field.text = ''

        self.reverse_search_mode = False
        self.search_query = ''
        self.search_results = []
        self.search_index = 0

    def _update_status(self) -> None:
        """Update status panel"""
        try:
            status_path = self.home / "state" / "status.json"
            if status_path.exists():
                data = json.loads(status_path.read_text(encoding='utf-8'))
                h = data.get('handoffs') or {}
                lines = [
                    f"PeerA: {self.config.peerA}",
                    f"  handoffs: {h.get('handoffs_peerA', 0)}",
                    "",
                    f"PeerB: {self.config.peerB}",
                    f"  handoffs: {h.get('handoffs_peerB', 0)}",
                ]
                self.status.text = '\n'.join(lines)
            else:
                lines = [
                    f"PeerA: {self.config.peerA}",
                    f"PeerB: {self.config.peerB}",
                    "",
                    "Waiting for",
                    "orchestrator...",
                ]
                self.status.text = '\n'.join(lines)
        except Exception:
            self.status.text = "Status unavailable"

    def _process_command(self, text: str) -> None:
        """Process user command"""
        text = text.strip()
        if not text:
            return

        cmds = self.home / "state" / "commands.jsonl"
        cmds.parent.mkdir(parents=True, exist_ok=True)
        ts = time.time()

        # Parse command
        if text == '/help':
            help_text = """Commands:
  /a <message>    - Send message to PeerA only
  /b <message>    - Send message to PeerB only
  /both <message> - Send message to both peers
  /status         - Show orchestrator status
  /queue          - Show commit queue
  /locks          - Show path locks
  /help           - Show this help

Keyboard shortcuts:
  Up/Down         - Navigate command history
  Ctrl+R          - Reverse search history (type to search, Ctrl+R for next)
  Tab             - Command auto-completion
  Ctrl+A          - Jump to start of line
  Ctrl+E          - Jump to end of line
  Ctrl+W          - Delete word backwards
  Ctrl+U          - Delete to start of line
  Ctrl+K          - Delete to end of line
  Ctrl+L          - Clear screen
  Ctrl+G/Esc      - Cancel reverse search
  PageUp/PageDown - Scroll timeline

Exit CCCC:
  Ctrl+b d        - Detach from tmux session (exits entire orchestrator)
  Ctrl+Q          - Show exit instructions"""
            self._write_timeline(help_text, 'info')

        elif text == '/status':
            self._update_status()
            self._write_timeline("Status updated.", 'system')

        elif text == '/queue':
            self._write_timeline("/queue command sent to orchestrator.", 'system')
            with cmds.open('a', encoding='utf-8') as f:
                cmd = {"type": "queue", "args": {}, "source": "tui", "ts": ts}
                f.write(json.dumps(cmd, ensure_ascii=False) + '\n')

        elif text == '/locks':
            self._write_timeline("/locks command sent to orchestrator.", 'system')
            with cmds.open('a', encoding='utf-8') as f:
                cmd = {"type": "locks", "args": {}, "source": "tui", "ts": ts}
                f.write(json.dumps(cmd, ensure_ascii=False) + '\n')

        elif text.startswith('/a '):
            msg = text[3:].strip()
            if msg:
                self._write_timeline(f"You → PeerA: {msg}", 'user')
                with cmds.open('a', encoding='utf-8') as f:
                    cmd = {"type": "a", "args": {"text": msg}, "source": "tui", "ts": ts}
                    f.write(json.dumps(cmd, ensure_ascii=False) + '\n')
                    f.flush()
                self._write_timeline(f"Command written to {cmds}: {cmd}", 'debug')
            else:
                self._write_timeline("Message required. Usage: /a <message>", 'error')

        elif text.startswith('/b '):
            msg = text[3:].strip()
            if msg:
                self._write_timeline(f"You → PeerB: {msg}", 'user')
                with cmds.open('a', encoding='utf-8') as f:
                    cmd = {"type": "b", "args": {"text": msg}, "source": "tui", "ts": ts}
                    f.write(json.dumps(cmd, ensure_ascii=False) + '\n')
                    f.flush()
                self._write_timeline(f"Command written to {cmds}: {cmd}", 'debug')
            else:
                self._write_timeline("Message required. Usage: /b <message>", 'error')

        elif text.startswith('/both '):
            msg = text[6:].strip()
            if msg:
                self._write_timeline(f"You → Both: {msg}", 'user')
                with cmds.open('a', encoding='utf-8') as f:
                    cmd = {"type": "both", "args": {"text": msg}, "source": "tui", "ts": ts}
                    f.write(json.dumps(cmd, ensure_ascii=False) + '\n')
                    f.flush()
                self._write_timeline(f"Command written to {cmds}: {cmd}", 'debug')
            else:
                self._write_timeline("Message required. Usage: /both <message>", 'error')

        else:
            self._write_timeline(f"Unknown command: {text}. Type /help for help.", 'error')

    def _open_dialog(self, dialog: Dialog, ok_handler: Optional[callable] = None) -> None:
        """Open dialog"""
        if self.modal_open:
            return

        float_dialog = Float(content=dialog)
        self.floats.append(float_dialog)
        self.current_dialog = float_dialog
        self.modal_open = True
        self.dialog_ok_handler = ok_handler

        try:
            self.app.invalidate()
        except Exception:
            pass

    def _close_dialog(self) -> None:
        """Close dialog"""
        if not self.modal_open or not self.current_dialog:
            return

        try:
            self.floats.remove(self.current_dialog)
        except ValueError:
            pass

        self.current_dialog = None
        self.modal_open = False
        self.dialog_ok_handler = None

        # Refocus first button
        try:
            self.app.layout.focus(self.btn_peerA)
        except Exception:
            pass

        try:
            self.app.invalidate()
        except Exception:
            pass

    def _create_key_bindings(self) -> KeyBindings:
        """Key bindings"""
        kb = KeyBindings()

        # Note: TUI is part of tmux session, not standalone
        # Use Ctrl+b d to detach from tmux (which exits the whole cccc orchestrator)
        # Ctrl+C is reserved for interrupting CLI operations in peer panes

        @kb.add('c-q')
        def show_exit_help(event) -> None:
            """Show exit instructions"""
            self._write_timeline("To exit CCCC: Press Ctrl+b then d (detach from tmux session)", 'info')

        # Tab navigation (setup phase, non-modal)
        @kb.add('tab', filter=Condition(lambda: self.setup_visible and not self.modal_open))
        def next_button(event) -> None:
            try:
                focused = self.app.layout.current_window
                for i, btn in enumerate(self.buttons):
                    if btn == focused or hasattr(focused, 'content') and focused.content == btn:
                        next_idx = (i + 1) % len(self.buttons)
                        self.app.layout.focus(self.buttons[next_idx])
                        return
                self.app.layout.focus(self.buttons[0])
            except Exception:
                pass

        @kb.add('s-tab', filter=Condition(lambda: self.setup_visible and not self.modal_open))
        def prev_button(event) -> None:
            try:
                focused = self.app.layout.current_window
                for i, btn in enumerate(self.buttons):
                    if btn == focused or hasattr(focused, 'content') and focused.content == btn:
                        prev_idx = (i - 1) % len(self.buttons)
                        self.app.layout.focus(self.buttons[prev_idx])
                        return
                self.app.layout.focus(self.buttons[-1])
            except Exception:
                pass

        # Command history navigation (runtime phase, not in search mode)
        @kb.add('up', filter=~Condition(lambda: self.setup_visible or self.modal_open or self.reverse_search_mode) & has_focus(self.input_field))
        def history_prev(event) -> None:
            if not self.command_history:
                return
            # Save current input when starting to navigate
            if self.history_index == -1:
                self.current_input = self.input_field.text
            # Navigate to previous command
            if self.history_index < len(self.command_history) - 1:
                self.history_index += 1
                self.input_field.text = self.command_history[-(self.history_index + 1)]
                # Move cursor to end
                self.input_field.buffer.cursor_position = len(self.input_field.text)

        @kb.add('down', filter=~Condition(lambda: self.setup_visible or self.modal_open or self.reverse_search_mode) & has_focus(self.input_field))
        def history_next(event) -> None:
            if self.history_index == -1:
                return
            # Navigate to next command
            self.history_index -= 1
            if self.history_index == -1:
                # Restore current input
                self.input_field.text = self.current_input
            else:
                self.input_field.text = self.command_history[-(self.history_index + 1)]
            # Move cursor to end
            self.input_field.buffer.cursor_position = len(self.input_field.text)

        # Clear screen (runtime phase)
        @kb.add('c-l', filter=~Condition(lambda: self.setup_visible or self.modal_open))
        def clear_screen(event) -> None:
            timestamp = time.strftime('%H:%M:%S')
            self.timeline.text = f'{timestamp} ┃SYS┃ Screen cleared. Type /help for commands and shortcuts.\n'
            self.timeline.buffer.cursor_position = len(self.timeline.text)

        # Page Up/Down for timeline navigation
        @kb.add('pageup', filter=~Condition(lambda: self.setup_visible or self.modal_open))
        def page_up(event) -> None:
            """Scroll timeline up"""
            lines_count = self.timeline.text.count('\n')
            current_pos = self.timeline.buffer.cursor_position
            # Calculate line height and scroll up by ~10 lines
            new_pos = max(0, current_pos - 500)
            self.timeline.buffer.cursor_position = new_pos

        @kb.add('pagedown', filter=~Condition(lambda: self.setup_visible or self.modal_open))
        def page_down(event) -> None:
            """Scroll timeline down"""
            current_pos = self.timeline.buffer.cursor_position
            max_pos = len(self.timeline.text)
            # Scroll down by ~10 lines
            new_pos = min(max_pos, current_pos + 500)
            self.timeline.buffer.cursor_position = new_pos

        # Standard editing shortcuts (runtime phase, input focused, not in search mode)
        @kb.add('c-a', filter=~Condition(lambda: self.setup_visible or self.modal_open or self.reverse_search_mode) & has_focus(self.input_field))
        def jump_to_start(event) -> None:
            self.input_field.buffer.cursor_position = 0

        @kb.add('c-e', filter=~Condition(lambda: self.setup_visible or self.modal_open or self.reverse_search_mode) & has_focus(self.input_field))
        def jump_to_end(event) -> None:
            self.input_field.buffer.cursor_position = len(self.input_field.text)

        @kb.add('c-w', filter=~Condition(lambda: self.setup_visible or self.modal_open or self.reverse_search_mode) & has_focus(self.input_field))
        def delete_word(event) -> None:
            buffer = self.input_field.buffer
            # Delete word before cursor
            pos = buffer.cursor_position
            text = buffer.text[:pos]
            # Find last space or start
            words = text.rstrip().rsplit(' ', 1)
            if len(words) == 1:
                # Delete everything before cursor
                buffer.text = buffer.text[pos:]
                buffer.cursor_position = 0
            else:
                # Delete last word
                new_text = words[0] + ' ' + buffer.text[pos:]
                buffer.text = new_text
                buffer.cursor_position = len(words[0]) + 1

        @kb.add('c-u', filter=~Condition(lambda: self.setup_visible or self.modal_open or self.reverse_search_mode) & has_focus(self.input_field))
        def delete_to_start(event) -> None:
            buffer = self.input_field.buffer
            pos = buffer.cursor_position
            buffer.text = buffer.text[pos:]
            buffer.cursor_position = 0

        @kb.add('c-k', filter=~Condition(lambda: self.setup_visible or self.modal_open or self.reverse_search_mode) & has_focus(self.input_field))
        def delete_to_end(event) -> None:
            buffer = self.input_field.buffer
            pos = buffer.cursor_position
            buffer.text = buffer.text[:pos]

        # Ctrl+R reverse search
        @kb.add('c-r', filter=~Condition(lambda: self.setup_visible or self.modal_open or self.reverse_search_mode) & has_focus(self.input_field))
        def start_reverse_search(event) -> None:
            """Start reverse history search"""
            self._start_reverse_search()
            self._write_timeline("Reverse search mode: type to search, Ctrl+R for next match, Enter to accept, Ctrl+G to cancel", 'info')

        # In reverse search mode, regular characters update the search
        @kb.add('<any>', filter=Condition(lambda: self.reverse_search_mode))
        def search_input(event) -> None:
            """Handle character input during reverse search"""
            char = event.data
            if char and char.isprintable():
                self.search_query += char
                self._perform_reverse_search(self.search_query)

        # Backspace in reverse search
        @kb.add('backspace', filter=Condition(lambda: self.reverse_search_mode))
        def search_backspace(event) -> None:
            """Handle backspace during reverse search"""
            if self.search_query:
                self.search_query = self.search_query[:-1]
                self._perform_reverse_search(self.search_query)

        # Ctrl+R again cycles through results
        @kb.add('c-r', filter=Condition(lambda: self.reverse_search_mode))
        def next_search_result(event) -> None:
            """Cycle to next search result"""
            if self.search_results and len(self.search_results) > 1:
                self.search_index = (self.search_index + 1) % len(self.search_results)
                self._update_search_prompt()

        # Enter accepts the search result
        @kb.add('enter', filter=Condition(lambda: self.reverse_search_mode))
        def accept_search(event) -> None:
            """Accept reverse search result"""
            self._exit_reverse_search(accept=True)
            # Don't submit immediately, let user edit if needed

        # Ctrl+G or Escape cancels search
        @kb.add('c-g', filter=Condition(lambda: self.reverse_search_mode))
        @kb.add('escape', filter=Condition(lambda: self.reverse_search_mode))
        def cancel_search(event) -> None:
            """Cancel reverse search"""
            self._exit_reverse_search(accept=False)

        # Enter to submit command (runtime phase, non-modal, not in search)
        @kb.add('enter', filter=~Condition(lambda: self.setup_visible or self.modal_open or self.reverse_search_mode) & has_focus(self.input_field))
        def submit_command(event) -> None:
            text = self.input_field.text.strip()
            if text:
                # Add to history (avoid consecutive duplicates)
                if not self.command_history or self.command_history[-1] != text:
                    self.command_history.append(text)
                    # Limit history size
                    if len(self.command_history) > 1000:
                        self.command_history = self.command_history[-1000:]
            # Reset history navigation
            self.history_index = -1
            self.current_input = ''
            # Clear input and process
            self.input_field.text = ''
            if text:
                self._process_command(text)

        return kb

    async def refresh_loop(self) -> None:
        """Background refresh"""
        while True:
            if not self.setup_visible:
                # Refresh timeline from outbox.jsonl
                try:
                    outbox = self.home / "state" / "outbox.jsonl"
                    if outbox.exists():
                        lines = outbox.read_text(encoding='utf-8', errors='replace').splitlines()[-100:]
                        # Find new messages since last check
                        for ln in lines:
                            if not ln.strip():
                                continue
                            try:
                                ev = json.loads(ln)
                                if ev.get('type') in ('to_user', 'to_peer_summary'):
                                    frm = ev.get('from', ev.get('peer', '?')).lower()
                                    text = ev.get('text', '')

                                    # Determine message type based on source
                                    if frm == 'peera' or frm == 'a':
                                        msg_type = 'peerA'
                                    elif frm == 'peerb' or frm == 'b':
                                        msg_type = 'peerB'
                                    elif frm == 'system':
                                        msg_type = 'system'
                                    else:
                                        msg_type = 'info'

                                    # Avoid duplicate messages (check by content without timestamp)
                                    check_line = f"{frm}: {text}"
                                    if check_line not in self.timeline.text:
                                        self._write_timeline(f"{frm.upper()}: {text}", msg_type)
                            except Exception:
                                pass
                except Exception:
                    pass

                # Update status
                self._update_status()

            await asyncio.sleep(2.0)


def run(home: Path) -> None:
    """Entry point"""
    app = CCCCSetupApp(home)

    # Write ready flag
    try:
        p = home / "state" / "tui.ready"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(int(time.time())))
    except Exception:
        pass

    # Start refresh
    app.app.pre_run_callables.append(
        lambda: asyncio.get_event_loop().create_task(app.refresh_loop())
    )

    # Run
    app.app.run()
