#!/usr/bin/env python3
import os, re, glob, shutil, threading, queue, subprocess, datetime, json, tempfile, time, zipfile, pty, select, signal
from urllib.parse import quote
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

APP='Proxmark3 Workshop'
VERSION='0.71-36'
ANSI_RE=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')

class PM3:
    def __init__(self, emit): self.emit=emit; self.path=None; self.port=None; self.proc=None
    def find_pm3(self):
        candidates=[os.path.expanduser('~/proxmark3/pm3'),'/usr/local/bin/pm3','/usr/bin/pm3']
        p=shutil.which('pm3')
        if p: candidates.insert(0,p)
        for c in candidates:
            if c and os.path.isfile(c) and os.access(c,os.X_OK): return c
    def ports(self): return sorted(set(glob.glob('/dev/pm3-*')+glob.glob('/dev/ttyACM*')+glob.glob('/dev/ttyUSB*')))
    def configure(self,port=None):
        self.path=self.find_pm3(); self.port=port
        if not self.path: raise RuntimeError('Could not find pm3. Expected ~/proxmark3/pm3 or pm3 in PATH.')
    def execute(self,cmd,timeout=240,cwd=None):
        # Use a one-command PM3 script. RRG documents -s as executing the
        # command file and exiting afterwards, which gives the GUI a reliable
        # completion signal instead of leaving a client process at a prompt.
        script_path=None
        try:
            with tempfile.NamedTemporaryFile('w',suffix='.cmd',prefix='pm3-gui-',delete=False,encoding='utf-8') as f:
                # RRG's script mode does not reliably terminate the interactive client
                # after the final scripted command on every build.  Explicitly quit so
                # Workshop receives command completion and can parse/update the UI.
                f.write(cmd+'\n')
                f.write('quit\n')
                script_path=f.name
            args=[self.path]+(['-p',self.port] if self.port else [])+['-f','-s',script_path]
            self.emit(f'\n[GUI] > {cmd}\n')
            self.proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',bufsize=1,cwd=cwd)
            lines=[]
            try:
                for line in iter(self.proc.stdout.readline,''):
                    if not line and self.proc.poll() is not None: break
                    lines.append(line); self.emit(line)
                rc=self.proc.wait(timeout=timeout)
            finally:
                self.proc=None
            if rc:
                raise RuntimeError(f'PM3 exited with code {rc}. See raw console output.')
            return ''.join(lines)
        finally:
            if script_path:
                try: os.unlink(script_path)
                except OSError: pass

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:self.proc.terminate()
            except Exception:pass
        self.proc=None


class App(tk.Tk):
    BG='#06182a'; PANEL='#0a2239'; PANEL2='#0d2b47'; TEXT='#e9f2ff'; MUTED='#9fb5cf'; NAVY='#071a2d'; ACCENT='#1688ff'; GREEN='#25e66a'; RED='#ff5c68'; BORDER='#1d5b8d'; CONSOLE='#031321'
    def __init__(self):
        super().__init__(); self.title(f'{APP} v{VERSION}'); self.geometry('1366x720'); self.minsize(1100,640); self.configure(bg=self.BG)
        self.asset_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)),'assets'); self.app_icon=None; self.device_photo=None
        try:
            self.app_icon=tk.PhotoImage(file=os.path.join(self.asset_dir,'proxmark3-workshop.png')); self.iconphoto(True,self.app_icon)
        except Exception: pass
        self.q=queue.Queue(); self.pm=PM3(lambda s:self.q.put(('log',s))); self.connected=False; self.active_backend='pm3'; self.detected_devices=[]; self.busy=False; self.last_tag=None; self.raw_log=''; self.creator_window=None; self.raw_mode=False; self.history=[]; self.history_pos=0; self.current_op='Ready'; self.current_cmd=''; self.library_root=os.path.expanduser('~/Proxmark3 Backups'); self.backup_dir=os.path.join(self.library_root,'My Backups'); self.test_dump_dir=os.path.join(self.library_root,'Test Dumps')
        self.config_dir=os.path.expanduser('~/.config/proxmark3-workshop'); os.makedirs(self.config_dir,exist_ok=True)
        self.data_dir=os.path.expanduser('~/.local/share/proxmark3-workshop'); os.makedirs(self.data_dir,exist_ok=True)
        self.settings_path=os.path.join(self.config_dir,'settings.json'); self.job_history_path=os.path.join(self.data_dir,'history.json'); self.templates_path=os.path.join(self.data_dir,'templates.json'); self.notes_path=os.path.join(self.data_dir,'tag-notes.json'); self.tag_library_path=os.path.join(self.data_dir,'known-tags.json')
        self.keys_dir=os.path.join(self.data_dir,'keys'); os.makedirs(self.keys_dir,exist_ok=True)
        self.my_keys_dir=os.path.join(self.keys_dir,'my-keys'); os.makedirs(self.my_keys_dir,exist_ok=True)
        self.key_history_path=os.path.join(self.keys_dir,'import-history.json')
        self.settings=self._load_json(self.settings_path, {'raw_console':False,'remember_window':True,'check_updates_startup':True,'dismissed_update_revision':'','firmware_platform':'PM3GENERIC','firmware_extras':'FLASH','ui_mode':'Beginner','linked_backup_folders':[]}); self._initialise_managed_library(); self.linked_backup_folders=[os.path.abspath(os.path.expanduser(x)) for x in self.settings.get('linked_backup_folders',[]) if x]
        self.job_history=self._load_json(self.job_history_path, []); self.templates=self._load_json(self.templates_path, {}); self.tag_notes=self._load_json(self.notes_path,{}); self.tag_library=self._load_json(self.tag_library_path,{}); self.compare_tag_a=None
        # Built-in Proxmark3 NFC Studio template.
        if 'Pro Locks UK — Save Contact' not in self.templates:
            self.templates['Pro Locks UK — Save Contact']={'kind':'Website / URL','url':'https://prolocksuk.co.uk/save-contact/','name':'','company':'Pro Locks UK','phone':'','email':'','subject':'','message':'','ssid':'','wifi_pass':'','custom':'','wifi_sec':'WPA2'}
            self._save_json(self.templates_path,self.templates)
        self.raw_mode=bool(self.settings.get('raw_console',False)); self._last_ports=[]; self.update_proc=None; self.update_cancel=False
        self._closing=False; self.protocol('WM_DELETE_WINDOW',self._request_close)
        self._style(); self._menu(); self._ui(); self.after(50,self._drain); self.refresh_ports(); self.after(1500,self._device_watch); self.after(3500,self._startup_update_check)
    def _initialise_managed_library(self):
        """Create the fixed Workshop library and migrate legacy top-level dumps."""
        legacy=os.path.abspath(os.path.expanduser(self.settings.get('backup_dir','~/Proxmark3 Backups')))
        self.library_root=legacy
        self.backup_dir=os.path.join(self.library_root,'My Backups')
        self.test_dump_dir=os.path.join(self.library_root,'Test Dumps')
        os.makedirs(self.backup_dir,exist_ok=True); os.makedirs(self.test_dump_dir,exist_ok=True)
        legacy_updates=os.path.join(self.library_root,'firmware-updates')
        if os.path.isdir(legacy_updates):
            try: shutil.rmtree(legacy_updates)
            except OSError: pass
        # Migrate only loose dump/metadata files from the old root. Never move user folders.
        for name in list(os.listdir(self.library_root)):
            src=os.path.join(self.library_root,name)
            if not os.path.isfile(src): continue
            if name.lower().endswith(('.bin','.mfd','.dump','.eml','.json')):
                dest=os.path.join(self.backup_dir,name)
                if not os.path.exists(dest):
                    try: shutil.move(src,dest)
                    except OSError: pass
        # Install/refresh the bundled synthetic training set into the visible library.
        bundled=os.path.join(os.path.dirname(os.path.abspath(__file__)),'test-dumps')
        if os.path.isdir(bundled):
            for name in os.listdir(bundled):
                src=os.path.join(bundled,name); dest=os.path.join(self.test_dump_dir,name)
                if os.path.isfile(src):
                    try: shutil.copy2(src,dest)
                    except OSError: pass
        self.settings['backup_dir']=self.library_root
        self.settings['dump_library_chosen']=True
        self._save_json(self.settings_path,self.settings)

    def _library_sources(self):
        sources=[('My Backups',self.backup_dir)]
        for path in self.linked_backup_folders:
            label=os.path.basename(path.rstrip(os.sep)) or path
            sources.append((label,path))
        return sources

    def link_backup_folder(self):
        path=filedialog.askdirectory(title='Link Existing Backup Folder',initialdir=os.path.expanduser('~'),mustexist=True,parent=self)
        if not path:return
        path=os.path.abspath(os.path.expanduser(path))
        if path in (os.path.abspath(self.library_root),os.path.abspath(self.backup_dir),os.path.abspath(self.test_dump_dir)):
            messagebox.showinfo('Link Backup Folder','That folder is already part of the Workshop library.'); return
        if path not in self.linked_backup_folders:self.linked_backup_folders.append(path)
        self.settings['linked_backup_folders']=self.linked_backup_folders; self._save_json(self.settings_path,self.settings); self.refresh_saved_library()

    def manage_linked_folders(self):
        w=tk.Toplevel(self); w.title('Linked Backup Folders'); w.geometry('720x360'); w.transient(self)
        f=ttk.Frame(w,padding=14); f.pack(fill='both',expand=True)
        ttk.Label(f,text='LINKED BACKUP COLLECTIONS',style='TagTitle.TLabel').pack(anchor='w')
        ttk.Label(f,text='Linked folders are indexed only. Workshop never deletes their files when you unlink them.',style='Muted.TLabel').pack(anchor='w',pady=(3,8))
        lb=tk.Listbox(f,bg=self.CONSOLE,fg=self.TEXT,selectbackground=self.ACCENT,selectforeground='white'); lb.pack(fill='both',expand=True)
        def refresh():
            lb.delete(0,'end')
            for x in self.linked_backup_folders: lb.insert('end',x+('' if os.path.isdir(x) else '   [OFFLINE]'))
        def selected():
            s=lb.curselection(); return s[0] if s else None
        def unlink():
            i=selected()
            if i is None:return
            self.linked_backup_folders.pop(i); self.settings['linked_backup_folders']=self.linked_backup_folders; self._save_json(self.settings_path,self.settings); refresh(); self.refresh_saved_library()
        def openf():
            i=selected()
            if i is not None and os.path.isdir(self.linked_backup_folders[i]): subprocess.Popen(['xdg-open',self.linked_backup_folders[i]])
        bar=ttk.Frame(f); bar.pack(fill='x',pady=(8,0))
        ttk.Button(bar,text='LINK ANOTHER FOLDER',style='Primary.TButton',command=lambda:(w.destroy(),self.link_backup_folder())).pack(side='left')
        ttk.Button(bar,text='OPEN',style='Action.TButton',command=openf).pack(side='left',padx=6)
        ttk.Button(bar,text='UNLINK',style='Action.TButton',command=unlink).pack(side='left')
        ttk.Button(bar,text='RESCAN',style='Action.TButton',command=lambda:(refresh(),self.refresh_saved_library())).pack(side='right')
        refresh()

    def _style(self):
        s=ttk.Style(self)
        if 'clam' in s.theme_names(): s.theme_use('clam')
        s.configure('.',font=('Sans',10),background=self.BG,foreground=self.TEXT)
        s.configure('TFrame',background=self.BG)
        s.configure('Top.TFrame',background=self.NAVY)
        s.configure('Top.TLabel',background=self.NAVY,foreground=self.TEXT)
        s.configure('Title.Top.TLabel',font=('Sans',16,'bold'),background=self.NAVY,foreground='#f5f9ff')
        s.configure('Muted.TLabel',foreground=self.MUTED,background=self.PANEL)
        s.configure('Card.TFrame',background=self.PANEL)
        s.configure('Card2.TFrame',background=self.PANEL2)
        s.configure('Card.TLabel',background=self.PANEL,foreground=self.TEXT)
        s.configure('CardTitle.TLabel',font=('Sans',10,'bold'),background=self.PANEL,foreground='#79bdff')
        s.configure('TagTitle.TLabel',font=('Sans',14,'bold'),background=self.PANEL,foreground='#f3f8ff')
        s.configure('Primary.TButton',font=('Sans',11,'bold'),padding=(16,12),background=self.ACCENT,foreground='white',bordercolor='#2ba0ff',focuscolor=self.ACCENT)
        s.map('Primary.TButton',background=[('active','#269bff'),('disabled','#163b5c')],foreground=[('disabled','#7890aa')])
        s.configure('Nav.TButton',font=('Sans',10,'bold'),padding=(14,11),background='#0d2944',foreground=self.TEXT,bordercolor='#1b5684')
        s.map('Nav.TButton',background=[('active','#123b60')])
        s.configure('Action.TButton',padding=(10,8),background='#0d2944',foreground=self.TEXT,bordercolor='#1b5684')
        s.map('Action.TButton',background=[('active','#123b60')])
        s.configure('HomePrimary.TButton',font=('Sans',10,'bold'),padding=(10,7),background=self.ACCENT,foreground='white',bordercolor='#2ba0ff',focuscolor=self.ACCENT)
        s.map('HomePrimary.TButton',background=[('active','#269bff'),('disabled','#163b5c')],foreground=[('disabled','#7890aa')])
        s.configure('HomeAction.TButton',font=('Sans',9),padding=(8,6),background='#0d2944',foreground=self.TEXT,bordercolor='#1b5684')
        s.map('HomeAction.TButton',background=[('active','#123b60')])
        s.configure('Danger.TButton',padding=(10,8),background='#3a1821',foreground='#ffdce0')
        s.configure('TEntry',fieldbackground='#061827',foreground=self.TEXT,insertcolor='white',bordercolor=self.BORDER)
        s.configure('TCombobox',fieldbackground='#0d2944',background='#0d2944',foreground='#ffffff',arrowcolor='#ffffff',selectbackground='#1688ff',selectforeground='#ffffff')
        s.map('TCombobox',fieldbackground=[('readonly','#0d2944'),('disabled','#10263a')],foreground=[('readonly','#ffffff'),('disabled','#7890aa')],selectbackground=[('readonly','#1688ff')],selectforeground=[('readonly','#ffffff')])
        # Tk renders the opened Combobox list as a Listbox, outside ttk styling.
        # Set it globally so values remain readable before and after opening a dropdown.
        self.option_add('*TCombobox*Listbox.background','#0d2944')
        self.option_add('*TCombobox*Listbox.foreground','#ffffff')
        self.option_add('*TCombobox*Listbox.selectBackground','#1688ff')
        self.option_add('*TCombobox*Listbox.selectForeground','#ffffff')
        self.option_add('*TCombobox*Listbox.font','Sans 10')
        s.configure('TCheckbutton',background=self.PANEL,foreground=self.TEXT,font=('Sans',10))
        s.map('TCheckbutton',background=[('active',self.PANEL)],foreground=[('disabled','#7890aa'),('active','#ffffff')])
        s.configure('TNotebook',background=self.BG,borderwidth=0)
        s.configure('TNotebook.Tab',background='#0d2944',foreground=self.TEXT,padding=(14,8))
        s.map('TNotebook.Tab',background=[('selected',self.ACCENT),('active','#123b60')],foreground=[('selected','#ffffff'),('active','#ffffff')])
        s.configure('Horizontal.TProgressbar',troughcolor='#04111f',background=self.ACCENT,bordercolor='#236da6',lightcolor=self.ACCENT,darkcolor=self.ACCENT,thickness=18)
        s.configure('Tab.TButton',font=('Sans',10,'bold'),padding=(14,8),background='#0a2239',foreground=self.MUTED,bordercolor='#1b5684')
        s.map('Tab.TButton',background=[('active','#123b60')],foreground=[('active',self.TEXT)])
        s.configure('ActiveTab.TButton',font=('Sans',10,'bold'),padding=(14,8),background=self.ACCENT,foreground='white',bordercolor='#2ba0ff')
        s.configure('Treeview',background=self.PANEL,fieldbackground=self.PANEL,foreground=self.TEXT)
        s.configure('CompactNav.TButton',font=('Sans',9,'bold'),padding=(10,7),background='#0d2944',foreground=self.TEXT,bordercolor='#1b5684')
        s.configure('SideNav.TButton',font=('Sans',10),padding=(16,11),background='#081f35',foreground=self.TEXT,bordercolor='#123d61',anchor='w')
        s.map('SideNav.TButton',background=[('active','#104a7a')])
        s.configure('SideActive.TButton',font=('Sans',10,'bold'),padding=(16,11),background='#104a7a',foreground='white',bordercolor='#1688ff',anchor='w')
    def _menu(self):
        bar=tk.Menu(self)
        tools=tk.Menu(bar,tearoff=0); tools.add_command(label='Hardware version',command=lambda:self.run('hw version')); tools.add_command(label='Hardware status',command=lambda:self.run('hw status')); tools.add_command(label='Antenna tune',command=lambda:self.run('hw tune')); tools.add_separator(); tools.add_command(label='LF search',command=lambda:self.run('lf search')); tools.add_command(label='HF search',command=lambda:self.run('hf search')); tools.add_command(label='EMV reader',command=lambda:self.run('emv reader')); tools.add_separator(); tools.add_command(label='Key Dictionaries…',command=self.show_key_dictionary_manager); tools.add_command(label='Command Centre…',command=self.show_command_centre); tools.add_command(label='Expert Command Console…',command=self.show_expert_console); tools.add_command(label='Device Diagnostics…',command=self.show_device_diagnostics); tools.add_command(label='Save Diagnostic Report…',command=self.export_diagnostic_report); tools.add_command(label='Toggle Privacy Mode',command=self.toggle_privacy_mode); tools.add_command(label='Offline Help…',command=self.show_offline_help); tools.add_command(label='Smart Tag Workspace…',command=self.show_smart_tag_workspace); tools.add_command(label='Known Tag Library…',command=self.show_known_tag_library); tools.add_command(label='Compare Tag A / B…',command=self.compare_tags); tools.add_command(label='Backup Health…',command=self.show_backup_health); tools.add_command(label='Portable Workshop Backup…',command=self.show_portable_backup); tools.add_command(label='Restore Previous GUI…',command=self.show_gui_rollback); tools.add_command(label='Proxmark3 Updates…',command=self.open_updater); tools.add_command(label='Save console…',command=self.save_log)
        bar.add_cascade(label='Tools',menu=tools)
        view=tk.Menu(bar,tearoff=0); view.add_command(label='Clean console',command=lambda:self.set_console_mode(False)); view.add_command(label='Raw console',command=lambda:self.set_console_mode(True)); view.add_separator(); view.add_command(label='Clear console',command=self.clear_console); view.add_command(label='Scanner home',command=self.scanner_home); bar.add_cascade(label='View',menu=view)
        helpm=tk.Menu(bar,tearoff=0); helpm.add_command(label='Copy diagnostics',command=self.copy_diagnostics); helpm.add_command(label='Settings',command=self.open_settings); helpm.add_separator(); helpm.add_command(label='About',command=lambda:messagebox.showinfo(APP,f'{APP} v{VERSION}\nGraphical front end for RRG Proxmark3.')); bar.add_cascade(label='Help',menu=helpm)
        self.config(menu=bar)
    def _ui(self):
        # v0.71-02: single-navigation desktop layout.  The old top navigation and
        # duplicate home action buttons are replaced by one persistent sidebar.
        shell=ttk.Frame(self,style='Top.TFrame'); shell.pack(fill='both',expand=True)
        side=ttk.Frame(shell,style='Top.TFrame',padding=(8,10)); side.pack(side='left',fill='y'); self.main_nav=side
        main=ttk.Frame(shell,style='Card.TFrame'); main.pack(side='left',fill='both',expand=True); self.main_body=main

        ttk.Label(side,text='PROXMARK3',style='Title.Top.TLabel',font=('Sans',16,'bold')).pack(anchor='w',padx=10,pady=(3,0))
        ttk.Label(side,text='WORKSHOP',style='Top.TLabel',foreground='#79bdff',font=('Sans',9,'bold')).pack(anchor='w',padx=10,pady=(0,14))
        self._side_buttons=[]
        def navbtn(text,cmd,active=False):
            b=ttk.Button(side,text=text,style='SideActive.TButton' if active else 'SideNav.TButton',command=cmd,width=22); b.pack(fill='x',pady=2); self._side_buttons.append(b); return b
        self.nav_buttons={}
        def pagebtn(key,text,cmd,active=False):
            b=navbtn(text,cmd,active); self.nav_buttons[key]=b; return b
        pagebtn('home','⌂  Home',self.scanner_home,True)
        self.scan_btn=pagebtn('studio','◉  NFC Tag Studio',self.open_nfc_creator)
        self.scan_hf_btn=self.scan_btn
        self.create_btn=self.scan_btn
        pagebtn('saved','▤  Saved Cards & Dumps',self.show_saved_page)
        pagebtn('tools','⚒  Tools',self.show_utilities)
        self.rf_lab_btn=pagebtn('rflab','⌁  RF Lab',self.show_rf_lab)
        pagebtn('update','▦  Firmware / Update',self.show_update_page)
        pagebtn('settings','⚙  Settings',self.show_settings_page)
        pagebtn('help','?  Help & Info',self.show_help_page)
        ttk.Frame(side,style='Top.TFrame').pack(fill='both',expand=True)
        ttk.Label(side,text=f'v{VERSION}',style='Top.TLabel',foreground=self.MUTED).pack(anchor='center',pady=(4,0))
        ttk.Label(side,text='Open Source  •  RRG',style='Top.TLabel',foreground=self.MUTED,font=('Sans',8)).pack(anchor='center',pady=(0,5))

        top=ttk.Frame(main,style='Top.TFrame',padding=(18,9,18,8)); top.pack(fill='x')
        brand=ttk.Frame(top,style='Top.TFrame'); brand.pack(side='left',fill='x',expand=True)
        ttk.Label(brand,text='RFID WORKSHOP',style='Title.Top.TLabel',font=('Sans',17,'bold')).pack(anchor='w')
        ttk.Label(brand,text='Scan • Identify • Read • Write • Verify',style='Top.TLabel',foreground='#79bdff',font=('Sans',9)).pack(anchor='w')
        chooser=ttk.Frame(top,style='Top.TFrame'); chooser.pack(side='right')
        self.devicebox=chooser; self.device_choice='pm3'; self.device_cards={}; self.device_photos={}
        self.header_connect_btn=tk.Button(chooser,text='●  CONNECT PROXMARK3',command=self.toggle_connect,bg=self.ACCENT,fg='white',activebackground='#269bff',activeforeground='white',font=('Sans',10,'bold'),relief='flat',bd=0,padx=22,pady=10,cursor='hand2',width=22); self.header_connect_btn.pack()
        self.device_image=ttk.Label(top); self.device_model=ttk.Label(top,text='Proxmark3'); self.device_state=ttk.Label(top,text=''); self.device_details=ttk.Label(top,text=''); self.activity=ttk.Label(top,text='Ready'); self.status=ttk.Label(top,text=''); self.device=ttk.Label(top,text='')
        self.connect_btn=ttk.Button(top,text='CONNECT',command=self.toggle_connect); self.port=ttk.Combobox(top,state='readonly'); self.stop_btn=ttk.Button(top,text='STOP',command=self.stop_operation,state='disabled')

        body=ttk.Frame(main,style='Card.TFrame',padding=(18,8,18,4)); body.pack(fill='both',expand=True); self.home_body=body
        workspace=ttk.Frame(body,style='Card.TFrame'); workspace.pack(fill='both',expand=True)
        # Home starts with the two primary scan actions. They stay fixed and visible.
        scan_card=ttk.Frame(workspace,style='Card.TFrame'); scan_card.pack(fill='x',pady=(0,7))
        ttk.Label(scan_card,text='SCAN & IDENTIFY',style='CardTitle.TLabel').pack(anchor='w',pady=(0,4))
        self.scan_actions=ttk.Frame(scan_card,style='Card.TFrame'); self.scan_actions.pack(fill='x')
        upper=ttk.Frame(workspace,style='Card.TFrame'); upper.pack(fill='both',expand=True)
        upper.columnconfigure(0,weight=2,minsize=360); upper.columnconfigure(1,weight=3,minsize=470); upper.rowconfigure(0,weight=1)
        work=ttk.Frame(upper,style='Card2.TFrame',padding=(16,12)); work.grid(row=0,column=0,sticky='nsew',padx=(0,10))
        taghead=ttk.Frame(work,style='Card2.TFrame'); taghead.pack(fill='x')
        ttk.Label(taghead,text='DETECTED CARD / TAG',background=self.PANEL2,foreground='#79bdff',font=('Sans',10,'bold')).pack(side='left')
        self.tag_badge=ttk.Label(taghead,text='● Ready',background=self.PANEL2,foreground=self.MUTED,font=('Sans',9,'bold')); self.tag_badge.pack(side='right')
        self.tag_title=ttk.Label(work,text='Ready to scan',background=self.PANEL2,foreground=self.TEXT,font=('Sans',16,'bold'),wraplength=350); self.tag_title.pack(anchor='w',pady=(12,5))
        self.tag_meta=ttk.Label(work,text='Place the card/tag on the correct Proxmark3 antenna, then choose an LF or HF scan.',background=self.PANEL2,foreground=self.MUTED,justify='left',wraplength=350); self.tag_meta.pack(anchor='w',fill='x')
        ttk.Separator(work).pack(fill='x',pady=13)
        ttk.Label(work,text='STATUS',background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')).pack(anchor='w')
        self.card_hint=ttk.Label(work,text='No card selected. Workshop will automatically choose the correct tools after a scan.',background=self.PANEL2,foreground=self.MUTED,justify='left',wraplength=350); self.card_hint.pack(anchor='w',fill='x',pady=(5,0))

        action_card=ttk.Frame(upper,style='Card.TFrame',padding=(14,4)); action_card.grid(row=0,column=1,sticky='nsew')
        self.action_heading=ttk.Label(action_card,text='AVAILABLE ACTIONS',style='CardTitle.TLabel'); self.action_heading.pack(anchor='w',pady=(0,4))
        self.actions=ttk.Frame(action_card,style='Card.TFrame'); self.actions.pack(fill='both',expand=True); self.action_canvas=None; self.action_scroll=None; self._home_actions()

        console_wrap=ttk.Frame(workspace,style='Card.TFrame',padding=(0,8,0,0)); console_wrap.pack(fill='x'); self.console_wrap=console_wrap
        chead=ttk.Frame(console_wrap,style='Card.TFrame'); chead.pack(fill='x',pady=(0,5))
        ttk.Label(chead,text='OUTPUT / LOG',style='CardTitle.TLabel').pack(side='left',padx=(0,10))
        self.console_title=ttk.Button(chead,text='Clean Output',style='ActiveTab.TButton',command=lambda:self.set_console_mode(False)); self.console_title.pack(side='left')
        self.console_mode_btn=ttk.Button(chead,text='Technical Details',style='Tab.TButton',command=lambda:self.set_console_mode(True)); self.console_mode_btn.pack(side='left',padx=(5,0))
        ttk.Button(chead,text='EXPAND',style='Action.TButton',command=self.toggle_console_size).pack(side='right'); ttk.Button(chead,text='Save Log',style='Action.TButton',command=self.save_log).pack(side='right',padx=6); ttk.Button(chead,text='Clear',style='Action.TButton',command=self.clear_console).pack(side='right')
        self.console=tk.Text(console_wrap,bg=self.CONSOLE,fg='#c9def6',insertbackground='white',selectbackground='#174a73',relief='flat',highlightthickness=1,highlightbackground='#174a73',padx=12,pady=8,font=('Monospace',9),wrap='word',height=4); self.console.pack(fill='x'); self.console_expanded=False
        cmdbar=ttk.Frame(console_wrap,style='Card.TFrame'); self.cmdbar=cmdbar; cmdbar.pack(fill='x',before=self.console,pady=(0,5)); ttk.Label(cmdbar,text='Manual Command   pm3 >',style='Card.TLabel').pack(side='left'); self.cmd=ttk.Entry(cmdbar,font=('Monospace',10)); self.cmd.pack(side='left',fill='x',expand=True,padx=8); self.cmd.bind('<Return>',lambda e:self.send_manual()); self.cmd.bind('<Up>',self.history_up); self.cmd.bind('<Down>',self.history_down); ttk.Button(cmdbar,text='SEND',style='Action.TButton',command=self.send_manual).pack(side='right')
        self.op_panel=ttk.Frame(console_wrap,style='Card2.TFrame',padding=(12,9)); ophead=ttk.Frame(self.op_panel,style='Card2.TFrame'); ophead.pack(fill='x'); self.op_title=ttk.Label(ophead,text='Reading Tag…',background=self.PANEL2,foreground='#f4f8ff',font=('Sans',12,'bold')); self.op_title.pack(side='left'); self.op_elapsed=ttk.Label(ophead,text='Elapsed 00:00',background=self.PANEL2,foreground=self.MUTED); self.op_elapsed.pack(side='right'); self.op_detail=ttk.Label(self.op_panel,text='Identifying card technology…',background=self.PANEL2,foreground='#b8d7f7'); self.op_detail.pack(anchor='w',pady=(3,3)); self.op_bar=ttk.Progressbar(self.op_panel,mode='indeterminate',style='Horizontal.TProgressbar'); self.op_bar.pack(fill='x',ipady=3,pady=(3,4)); self.op_stage=ttk.Label(self.op_panel,text='Detecting field • Searching • Reading • Checking • Complete',background=self.PANEL2,foreground='#79bdff',font=('Sans',8,'bold'),wraplength=1100); self.op_stage.pack(anchor='w'); self.op_started=0; self.op_last_activity=0; self.op_active=False; self.op_panel.pack_forget()
        self.update_panel=ttk.Frame(console_wrap,style='Card2.TFrame',padding=(12,9)); up_top=ttk.Frame(self.update_panel,style='Card2.TFrame'); up_top.pack(fill='x'); self.update_title=ttk.Label(up_top,text='PROXMARK3 UPDATE',background=self.PANEL2,foreground=self.TEXT,font=('Sans',12,'bold')); self.update_title.pack(side='left'); self.update_percent=ttk.Label(up_top,text='0%',background=self.PANEL2,foreground='#3da5ff',font=('Sans',12,'bold')); self.update_percent.pack(side='right'); self.update_overall=ttk.Progressbar(self.update_panel,mode='determinate',maximum=100,style='Horizontal.TProgressbar'); self.update_overall.pack(fill='x',ipady=3,pady=(5,4)); self.update_headline=ttk.Label(self.update_panel,text='Preparing update…',background=self.PANEL2,foreground=self.TEXT,font=('Sans',9,'bold')); self.update_headline.pack(anchor='w'); self.update_safety=ttk.Label(self.update_panel,text='SAFE TO DISCONNECT — not flashing',background=self.PANEL2,foreground=self.MUTED,font=('Sans',8)); self.update_safety.pack(anchor='w',pady=(1,4)); stagebox=ttk.Frame(self.update_panel,style='Card2.TFrame'); stagebox.pack(fill='x'); self.update_stage_labels=[]
        for i in range(10):
            lbl=ttk.Label(stagebox,text='',background=self.PANEL2,foreground=self.TEXT,anchor='w',font=('Sans',8)); lbl.grid(row=i//2,column=i%2,sticky='ew',padx=(0,10),pady=0); self.update_stage_labels.append(lbl)
        stagebox.columnconfigure(0,weight=1); stagebox.columnconfigure(1,weight=1); self.update_stage_progress=ttk.Progressbar(self.update_panel,mode='indeterminate',style='Horizontal.TProgressbar'); self.update_stage_progress.pack(fill='x',pady=(5,2)); self.update_timing=ttk.Label(self.update_panel,text='',background=self.PANEL2,foreground=self.MUTED,font=('Sans',8)); self.update_timing.pack(anchor='w'); self.update_panel.pack_forget()

        # Compact universal activity strip. Normal operations use a spinner + text;
        # genuine long workflows (firmware/batch/backup) keep their own real progress.
        global_activity=ttk.Frame(main,style='Card2.TFrame',padding=(18,4)); global_activity.pack(fill='x',padx=18,pady=(0,2)); self.global_activity=global_activity
        self.global_spinner=ttk.Label(global_activity,text='●',background=self.PANEL2,foreground=self.MUTED,font=('Sans',10,'bold')); self.global_spinner.pack(side='left',padx=(0,7))
        self.global_activity_title=ttk.Label(global_activity,text='READY',background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')); self.global_activity_title.pack(side='left')
        self.global_activity_detail=ttk.Label(global_activity,text=' — Choose a page or scan a tag.',background=self.PANEL2,foreground=self.MUTED,anchor='w'); self.global_activity_detail.pack(side='left',fill='x',expand=True)
        self.global_activity_elapsed=ttk.Label(global_activity,text='',background=self.PANEL2,foreground=self.MUTED,font=('Sans',8)); self.global_activity_elapsed.pack(side='right')
        self._global_activity_working=False; self._global_spin_index=0

        foot=ttk.Frame(main,style='Top.TFrame',padding=(18,3)); foot.pack(fill='x'); self.footer_status=ttk.Label(foot,text='●  Not connected',style='Top.TLabel'); self.footer_status.pack(side='left'); self.mode_btn=ttk.Button(foot,text='MODE: '+str(self.settings.get('ui_mode','Beginner')).upper(),style='Action.TButton',command=self.toggle_ui_mode); self.mode_btn.pack(side='right'); ttk.Label(foot,text=f'RRG  •  Open Source  •  v{VERSION}',style='Top.TLabel').pack(side='right',padx=(0,10)); self.after(100,self._apply_ui_mode)
    def _select_page(self,key):
        self.active_page=key
        for k,b in getattr(self,'nav_buttons',{}).items():
            try:b.config(style='SideActive.TButton' if k==key else 'SideNav.TButton')
            except Exception:pass

    def _clear_right_page(self,keep_home=False):
        for name in ('creator_window','tools_page','rf_lab_page','saved_page','update_page','settings_page','help_page'):
            w=getattr(self,name,None)
            if w is not None:
                try:
                    if w.winfo_exists(): w.destroy()
                except Exception:pass
                setattr(self,name,None)
        if not keep_home:
            try:self.home_body.pack_forget()
            except Exception:pass

    def _activity_feedback(self,title,detail='',working=False,success=None):
        if not hasattr(self,'global_activity_title'):return
        self._global_activity_working=bool(working)
        prefix='WORKING — ' if working else ('✓ ' if success is True else ('✗ ' if success is False else ''))
        colour=('#79bdff' if success is None else (self.GREEN if success else self.RED))
        self.global_activity_title.config(text=prefix+title.upper(),foreground=colour)
        self.global_activity_detail.config(text=' — '+(detail or title))
        if working:
            self._global_activity_started=time.time(); self._global_spin_index=0; self._tick_global_activity()
        else:
            self.global_activity_elapsed.config(text='')
            self.global_spinner.config(text=('✓' if success is True else ('✕' if success is False else '●')),foreground=(colour if success is not None else self.MUTED))

    def _tick_global_activity(self):
        if not getattr(self,'_global_activity_working',False):return
        try:
            frames=('◐','◓','◑','◒'); self._global_spin_index=(getattr(self,'_global_spin_index',0)+1)%len(frames)
            self.global_spinner.config(text=frames[self._global_spin_index],foreground=self.ACCENT)
            sec=int(time.time()-getattr(self,'_global_activity_started',time.time())); self.global_activity_elapsed.config(text=f'{sec//60:02d}:{sec%60:02d}')
            self.after(180,self._tick_global_activity)
        except Exception:pass

    def _simple_page(self,key,title,subtitle):
        self._clear_right_page(); self._select_page(key)
        page=ttk.Frame(self.main_body,style='Card.TFrame'); setattr(self,key+'_page',page); page.pack(fill='both',expand=True)
        outer=ttk.Frame(page,style='Card.TFrame',padding=(18,14)); outer.pack(fill='both',expand=True)
        ttk.Label(outer,text=title,style='TagTitle.TLabel',font=('Sans',18,'bold')).pack(anchor='w')
        ttk.Label(outer,text=subtitle,style='Muted.TLabel',wraplength=950).pack(anchor='w',pady=(3,12))
        self._activity_feedback(title,'Page ready.',False,None)
        return outer

    def show_saved_page(self):
        outer=self._simple_page('saved','SAVED CARDS & DUMPS','Open your saved backups, load external dumps and use built-in synthetic test data from one place.')
        box=ttk.Frame(outer,style='Card2.TFrame',padding=(10,8)); box.pack(fill='x')
        ttk.Label(box,text='LIBRARY ACTIONS',background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')).pack(anchor='w',pady=(0,5))
        row=ttk.Frame(box,style='Card2.TFrame'); row.pack(fill='x')
        actions=(('OPEN EXTERNAL DUMP',self.open_external_dump),('LINK BACKUP FOLDER',self.link_backup_folder),('MANAGE LINKED FOLDERS',self.manage_linked_folders),('OPEN LIBRARY',lambda:subprocess.Popen(['xdg-open',self.library_root])))
        for i,(t,fn) in enumerate(actions):
            ttk.Button(row,text=t,style='Action.TButton',command=lambda f=fn,n=t:self._page_action(n,f)).grid(row=0,column=i,sticky='ew',padx=3,pady=1); row.columnconfigure(i,weight=1)

        tabs=ttk.Notebook(outer); tabs.pack(fill='both',expand=True,pady=(8,0))
        mine=ttk.Frame(tabs,style='Card2.TFrame',padding=(10,8)); tests=ttk.Frame(tabs,style='Card2.TFrame',padding=(10,8))
        tabs.add(mine,text='MY SAVED DUMPS'); tabs.add(tests,text='TEST DATA')
        head=ttk.Frame(mine,style='Card2.TFrame'); head.pack(fill='x',pady=(0,5))
        ttk.Label(head,text='SAVED DUMPS',background=self.PANEL2,foreground='#79bdff',font=('Sans',10,'bold')).pack(side='left')
        self.saved_count_label=ttk.Label(head,text='',background=self.PANEL2,foreground=self.MUTED); self.saved_count_label.pack(side='left',padx=10)
        ttk.Label(head,text='Double-click a dump to open its workspace',background=self.PANEL2,foreground=self.MUTED).pack(side='right')
        filterrow=ttk.Frame(mine,style='Card2.TFrame'); filterrow.pack(fill='x',pady=(0,5))
        ttk.Label(filterrow,text='Library:',background=self.PANEL2,foreground=self.MUTED).pack(side='left')
        self.saved_folder_label=ttk.Label(filterrow,text=self.library_root,background=self.PANEL2,foreground=self.TEXT); self.saved_folder_label.pack(side='left',padx=(5,14))
        ttk.Label(filterrow,text='Search:',background=self.PANEL2,foreground=self.MUTED).pack(side='left')
        self.saved_filter_var=tk.StringVar(); ttk.Entry(filterrow,textvariable=self.saved_filter_var,width=24).pack(side='left',padx=5)
        ttk.Button(filterrow,text='REFRESH',style='HomeAction.TButton',command=self.refresh_saved_library).pack(side='right')
        table=ttk.Frame(mine,style='Card2.TFrame'); table.pack(fill='both',expand=True)
        cols=('type','uid','source','size','date'); tree=ttk.Treeview(table,columns=cols,show='tree headings',selectmode='browse')
        tree.heading('#0',text='Name / dump file'); tree.column('#0',width=330,minwidth=180,anchor='w')
        for c,h,w in [('type','Technology',190),('uid','UID / ID',165),('source','Source',145),('size','Size',80),('date','Saved',145)]: tree.heading(c,text=h); tree.column(c,width=w,minwidth=70,anchor='w')
        y=ttk.Scrollbar(table,orient='vertical',command=tree.yview); x=ttk.Scrollbar(table,orient='horizontal',command=tree.xview); tree.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        tree.grid(row=0,column=0,sticky='nsew'); y.grid(row=0,column=1,sticky='ns'); x.grid(row=1,column=0,sticky='ew'); table.rowconfigure(0,weight=1); table.columnconfigure(0,weight=1)
        self.saved_tree=tree; self.saved_paths={}; tree.bind('<Double-1>',self._saved_double_click); tree.bind('<Return>',lambda e:self.open_selected_saved_dump()); tree.bind('<Button-3>',self._saved_context_menu); tree.bind('<Button-2>',self._saved_context_menu)
        self.saved_filter_var.trace_add('write',lambda *_:self.refresh_saved_library()); self.refresh_saved_library()

        ttk.Label(tests,text='WORKSHOP TEST DATA',background=self.PANEL2,foreground='#79bdff',font=('Sans',10,'bold')).pack(anchor='w')
        ttk.Label(tests,text='Synthetic data only — never copied from a real credential. Memory samples are offline viewer/compare data; physical programming tests use the technology-specific native RRG workflow.',background=self.PANEL2,foreground=self.MUTED,wraplength=950).pack(anchor='w',pady=(2,6))
        tt=ttk.Treeview(tests,columns=('type','purpose','status'),show='headings',selectmode='browse')
        for c,h,w in [('type','Technology / Test',270),('purpose','Purpose',520),('status','Restore route',170)]:tt.heading(c,text=h);tt.column(c,width=w,anchor='w')
        tt.pack(fill='both',expand=True); self.test_dump_tree=tt; self.test_dump_paths={}
        for i,item in enumerate(self._test_dump_catalog()):
            iid=f't{i}'; tt.insert('', 'end',iid=iid,values=(item['name'],item['purpose'],item['status'])); self.test_dump_paths[iid]=item.get('path')
        tt.bind('<Double-1>',lambda e:self._open_selected_test_dump())
        br=ttk.Frame(tests,style='Card2.TFrame'); br.pack(fill='x',pady=(6,0))
        ttk.Button(br,text='OPEN TEST DATA',style='Primary.TButton',command=self._open_selected_test_dump).pack(side='left')
        ttk.Button(br,text='OPEN TEST DATA FOLDER',style='Action.TButton',command=lambda:subprocess.Popen(['xdg-open',self.test_dump_dir])).pack(side='left',padx=6)
        ttk.Button(br,text='RESTORE ORIGINAL TEST DATA',style='Action.TButton',command=self.restore_original_test_dumps).pack(side='left')
        ttk.Button(br,text='OPEN EXTERNAL DUMP…',style='Action.TButton',command=self.open_external_dump).pack(side='right')

    def restore_original_test_dumps(self):
        bundled=os.path.join(os.path.dirname(os.path.abspath(__file__)),'test-dumps')
        os.makedirs(self.test_dump_dir,exist_ok=True)
        count=0
        if os.path.isdir(bundled):
            for name in os.listdir(bundled):
                src=os.path.join(bundled,name); dest=os.path.join(self.test_dump_dir,name)
                if os.path.isfile(src): shutil.copy2(src,dest); count+=1
        messagebox.showinfo('Test Data',f'Restored {count} original Workshop test files to:\n{self.test_dump_dir}')
        self.show_saved_page()

    def _test_dump_catalog(self):
        """Synthetic regression/test material for every Workshop-supported family.

        A row is deliberately one of: memory sample, native-program test credential,
        or protocol/profile metadata.  The catalogue never implies that every RFID
        technology has a universal writable dump.
        """
        d=self.test_dump_dir
        memory=[
          ('MIFARE Classic 1K','RRG-compatible training image: default FFFFFFFFFFFF keys, valid transport access bits, recognisable PLUK test data. Block 0 is preserved on normal cards.','Writable test image + verify','PLUK_MIFARE_Classic_1K_TEST.bin'),
          ('MIFARE Classic 4K','RRG-compatible training image: default FFFFFFFFFFFF keys, valid transport access bits, recognisable PLUK test data. Block 0 is preserved on normal cards.','Writable test image + verify','PLUK_MIFARE_Classic_4K_TEST.bin'),
          ('T5577 / T55xx — EM410x','Use Home → PROGRAM EM410X TEST / ID; native RRG encode then decoded-ID verification.','Native program + verify','PLUK_T5577_GENERIC_TEST.json'),
          ('NTAG213','Synthetic viewer/import sample; model configuration pages are not universal.','Offline memory sample','PLUK_NTAG213_TEST.bin'),
          ('NTAG215','Synthetic viewer/import sample; model configuration pages are not universal.','Offline memory sample','PLUK_NTAG215_TEST.bin'),
          ('NTAG216','Synthetic viewer/import sample; model configuration pages are not universal.','Offline memory sample','PLUK_NTAG216_TEST.bin'),
          ('MIFARE Plus','Synthetic memory sample; security level and keys are card-specific.','Offline memory sample','PLUK_MIFARE_Plus_TEST.bin'),
          ('MIFARE DESFire','Synthetic profile only; applications/files/keys are card-specific.','Offline memory sample','PLUK_DESFire_OFFLINE_TEST.bin'),
          ('ISO15693 / NFC-V','Synthetic block-memory sample; block size/count varies by chip.','Offline memory sample','PLUK_ISO15693_TEST.bin'),
          ('iCLASS / Picopass','Synthetic block sample; authentication/configuration varies.','Offline memory sample','PLUK_iCLASS_TEST.bin'),
          ('LEGIC Prime','Synthetic memory sample; segment layout varies.','Offline memory sample','PLUK_LEGIC_TEST.bin'),
          ('ISO14443-B','Synthetic memory sample; Type-B products have different layouts.','Offline memory sample','PLUK_ISO14443B_TEST.bin'),
          ('FeliCa','Synthetic service/block sample; real services/keys vary.','Offline memory sample','PLUK_FeliCa_TEST.bin'),
          ('Topaz / NFC Type 1','Synthetic small-memory viewer sample.','Offline memory sample','PLUK_Topaz_Type1_TEST.bin'),
          ('LTO Cartridge Memory','Synthetic memory viewer sample.','Offline memory sample','PLUK_LTO_TEST.bin'),
          ('EM4x05 / EM4x69','Synthetic LF word-memory viewer sample.','Offline memory sample','PLUK_EM4x05_TEST.bin'),
          ('EM4x50','Synthetic LF word-memory viewer sample.','Offline memory sample','PLUK_EM4x50_TEST.bin'),
          ('Hitag 2 / family','Synthetic memory viewer sample; variants/auth differ.','Offline memory sample','PLUK_Hitag_TEST.bin'),
          ('NTAG 424 DNA','Synthetic profile only; secure files/authentication are card-specific.','Offline profile','PLUK_NTAG424_OFFLINE_TEST.bin'),
          ('ST25TA / NFC Type 4','Synthetic profile; NDEF/file layout depends on configuration.','Offline profile','PLUK_ST25TA_OFFLINE_TEST.bin'),
        ]
        credentials=[
          ('EM410x','1122334455','lf em 410x reader','lf em 410x clone --id 1122334455'),
          ('HID Prox','synthetic test credential','lf hid reader','lf hid clone'),
          ('AWID','synthetic test credential','lf awid reader','lf awid clone'),
          ('Indala','synthetic test credential','lf indala reader','lf indala clone'),
          ('FDX-B','synthetic test credential','lf fdxb reader','lf fdxb clone'),
          ('NEDAP','synthetic test credential','lf nedap reader','lf nedap clone'),
          ('Paradox','synthetic test credential','lf paradox reader','lf paradox clone'),
          ('Presco','synthetic test credential','lf presco reader','lf presco clone'),
          ('Securakey','synthetic test credential','lf securakey reader','lf securakey clone'),
          ('Jablotron','synthetic test credential','lf jablotron reader','lf jablotron clone'),
          ('KERI','synthetic test credential','lf keri reader','lf keri clone'),
          ('G-Prox II','synthetic test credential','lf gproxii reader','lf gproxii clone'),
          ('ioProx','synthetic test credential','lf io reader','lf io clone'),
          ('Pyramid','synthetic test credential','lf pyramid reader','lf pyramid clone'),
          ('Viking','synthetic test credential','lf viking reader','lf viking clone'),
          ('Trovan','synthetic test credential','lf trovan reader','protocol-specific'),
          ('PAC / Stanley','synthetic test credential','lf pac reader','protocol-specific'),
          ('Destron','synthetic test credential','lf destron reader','protocol-specific'),
          ('IDTECK','synthetic test credential','lf idteck reader','protocol-specific'),
          ('Motorola Flexpass','synthetic test credential','lf motorola reader','lf motorola clone'),
          ('NexWatch','synthetic test credential','lf nexwatch reader','protocol-specific'),
          ('Noralsy','synthetic test credential','lf noralsy reader','protocol-specific'),
          ('Visa2000','synthetic test credential','lf visa2000 reader','protocol-specific'),
        ]
        special=[
          ('EM4x70','Authenticated LF chip; no generic dump image.'),('Hitag S','Variant-specific memory/authentication.'),('Hitag µ / 8265','Variant-specific memory/authentication.'),
          ('Seos','Secure credential; no universal writable dump.'),('German eID / PACE','Secure application protocol.'),('FMCOS','CPU-card file/application workflow.'),
          ('ICT credential','Underlying MIFARE/DESFire workflow.'),('Jooki','Token-format workflow.'),('Apple VAS','Cryptographic application protocol.'),
          ('Waveshare NFC ePaper','Model-specific image workflow.'),('Gallagher HF','Underlying MIFARE/DESFire plus diversified keys.'),('CIPURSE','Secure file/application workflow.'),
          ('Calypso','Transport application/profile workflow.'),('Aliro','Cryptographic access credential.'),('eMRTD / ICAO','Protected travel-document workflow.'),
          ('FIDO / FIDO2','Authenticator protocol; no generic dump.'),('Google Smart Tap','Application/pass protocol.'),('KS X 6924','Transport application workflow.'),
          ('Saflok','Application on underlying MIFARE technology.'),('HID SE Config Card','Secure configuration-card workflow.'),('Tesla NFC','Application-dependent workflow.'),
          ('Texkom','Protocol-specific RFID workflow.'),('Thinfilm / NFC Barcode','NFC Barcode rather than generic memory dump.'),('Fuji/Xerox RFID','Model-specific cartridge memory.'),
          ('Fudan RFID','Model-specific memory/authentication.'),('PCF7931','Model/configuration-dependent LF transponder.'),('TI LF','Model/configuration-dependent LF transponder.'),
          ('COTAG','Protocol-specific LF credential.'),('Gallagher LF','Protocol-specific LF credential.'),
        ]
        rows=list(memory)
        for name,ident,reader,clone in credentials:
            fn='PLUK_CREDENTIAL_'+re.sub(r'[^A-Za-z0-9]+','_',name).strip('_')+'.json'
            rows.append((name,f'Synthetic credential test data. Reader: {reader}. Programming is offered only when Workshop has a current native RRG target route and can read it back.','Credential test',fn))
        for name,note in special:
            fn='PLUK_PROFILE_'+re.sub(r'[^A-Za-z0-9]+','_',name).strip('_')+'.json'
            rows.append((name,note+' This entry documents the correct test class instead of inventing a universal dump.','Profile / offline only',fn))
        return [{'name':a,'purpose':b,'status':c,'path':os.path.join(d,f)} for a,b,c,f in rows]

    def _open_selected_test_dump(self):
        tree=getattr(self,'test_dump_tree',None); sel=tree.selection() if tree else ()
        if not sel:return
        path=self.test_dump_paths.get(sel[0])
        if not path: messagebox.showinfo('Test Data','There is no safe universal dump image for this group. Scan the actual tag and Workshop will show the operations supported by that technology.'); return
        self.open_dump_viewer(path)

    def _infer_external_dump(self,path):
        ext=os.path.splitext(path)[1].lower(); meta={}
        if ext=='.json':
            try:
                obj=json.load(open(path,'r',encoding='utf-8'))
                ft=str(obj.get('FileType','')).lower()
                if 't55' in ft: meta={'kind':'t55xx','technology':'T5577 / T55xx','uid':'EM410x test' if '1122334455' in json.dumps(obj) else ''}
            except Exception:pass
        try:size=os.path.getsize(path)
        except OSError:size=0
        if not meta and size in (1024,4096):meta={'kind':'mfc','technology':'MIFARE Classic 1K' if size==1024 else 'MIFARE Classic 4K','uid':''}
        return meta

    def open_external_dump(self,import_to_library=False):
        path=filedialog.askopenfilename(title='Open External Proxmark Dump',initialdir=os.path.expanduser('~'),filetypes=[('Supported dumps','*.bin *.mfd *.dump *.eml *.json'),('All files','*.*')])
        if not path:return
        meta=self._infer_external_dump(path)
        if import_to_library:
            dest=os.path.join(self.backup_dir,os.path.basename(path)); root,ext=os.path.splitext(dest); n=2
            while os.path.exists(dest):dest=f'{root}_{n}{ext}';n+=1
            shutil.copy2(path,dest); meta.update({'created':datetime.datetime.now().isoformat(timespec='seconds'),'display_name':os.path.splitext(os.path.basename(path))[0],'dump_file':os.path.basename(dest),'files':[os.path.basename(dest)],'complete':True,'source':'External import'})
            self._save_saved_meta(dest,meta); self.refresh_saved_library(); messagebox.showinfo('Imported','External dump imported to your Workshop library.'); self.open_dump_viewer(dest); return
        self.open_dump_viewer(path)

    def _library_dump_files(self):
        # Show one logical backup per card. RRG companion key files (for example
        # MIFARE Classic *_keys.bin) belong to the card backup and must not appear
        # as a second saved card in the main library list.
        exts=('*.bin','*.mfd','*.dump','*.eml','*.json')
        files=[]
        
        self._dump_source_by_path={}
        for label,root in self._library_sources():
            if not os.path.isdir(root): continue
            for ext in exts:
                for fp in glob.glob(os.path.join(root,'**',ext),recursive=True):
                    files.append(fp); self._dump_source_by_path[os.path.abspath(fp)]=label
        out=[]
        for path in sorted(set(files),key=lambda x:os.path.getmtime(x),reverse=True):
            n=os.path.splitext(os.path.basename(path))[0].lower()
            if n.endswith('.workshop') or path.lower().endswith('.workshop.json'): continue
            if re.search(r'(?:^|[_\-.])keys?(?:[_\-.]|$)',n):
                continue
            out.append(path)
        return out

    def refresh_saved_library(self):
        tree=getattr(self,'saved_tree',None)
        if tree is None or not tree.winfo_exists(): return
        q=(getattr(self,'saved_filter_var',None).get().strip().lower() if getattr(self,'saved_filter_var',None) else '')
        for iid in tree.get_children(): tree.delete(iid)
        self.saved_paths={}; shown=0
        for n,path in enumerate(self._library_dump_files()):
            meta={}
            try:
                with open((path+'.workshop.json') if os.path.isfile(path+'.workshop.json') else (path+'.json'),'r',encoding='utf-8') as f: meta=json.load(f)
            except Exception: pass
            display=meta.get('display_name') or os.path.splitext(os.path.basename(path))[0]
            tech=meta.get('technology',''); uid=meta.get('uid','')
            hay=' '.join((display,os.path.basename(path),str(tech),str(uid))).lower()
            if q and q not in hay: continue
            iid=f'd{n}'; self.saved_paths[iid]=path
            size=os.path.getsize(path); size_txt=f'{size/1024:.1f} KB' if size>=1024 else f'{size} B'
            date=datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M')
            source=getattr(self,'_dump_source_by_path',{}).get(os.path.abspath(path),'My Backups'); tree.insert('', 'end',iid=iid,text=display,values=(tech,uid,source,size_txt,date)); shown+=1
        if hasattr(self,'saved_count_label'): self.saved_count_label.config(text=f'{shown} dump(s)')

    def _selected_saved_path(self):
        tree=getattr(self,'saved_tree',None)
        if tree is None:return None
        s=tree.selection(); return self.saved_paths.get(s[0]) if s else None

    def open_selected_saved_dump(self):
        path=self._selected_saved_path()
        if path:self.open_dump_viewer(path)

    def _saved_double_click(self,event):
        tree=getattr(self,'saved_tree',None)
        if tree is None:return
        iid=tree.identify_row(event.y)
        if not iid:return
        tree.selection_set(iid); tree.focus(iid)
        self.after_idle(self.open_selected_saved_dump)

    def _saved_meta_path(self,path):
        return path+'.workshop.json'

    def _saved_meta(self,path):
        meta={}
        for mp in (path+'.workshop.json',path+'.json'):
            if not os.path.isfile(mp):continue
            try:
                with open(mp,'r',encoding='utf-8') as f:
                    obj=json.load(f)
                if isinstance(obj,dict):meta=obj;break
            except Exception:pass
        return meta

    def _save_saved_meta(self,path,meta):
        with open(self._saved_meta_path(path),'w',encoding='utf-8') as f:json.dump(meta,f,indent=2)

    def _saved_companions(self,path):
        meta=self._saved_meta(path); out=[path]
        for name in meta.get('files',[]) if isinstance(meta.get('files',[]),list) else []:
            p=name if os.path.isabs(name) else os.path.join(os.path.dirname(path),name)
            if os.path.isfile(p) and p not in out:out.append(p)
        # Metadata is part of the logical backup too.
        mp=self._saved_meta_path(path)
        if os.path.isfile(mp):out.append(mp)
        return out

    def _saved_context_menu(self,event):
        tree=getattr(self,'saved_tree',None)
        if tree is None:return
        iid=tree.identify_row(event.y)
        if not iid:return
        tree.selection_set(iid); tree.focus(iid)
        path=self.saved_paths.get(iid)
        if not path:return
        m=tk.Menu(self,tearoff=0,bg=self.PANEL2,fg=self.TEXT,activebackground=self.ACCENT,activeforeground='white')
        m.add_command(label='Open / View Dump',command=lambda:self.open_dump_viewer(path))
        m.add_command(label='Card / Dump Information',command=lambda:self._saved_dump_info(path))
        m.add_separator()
        m.add_command(label='Rename Friendly Name',command=lambda:self._saved_rename(path))
        m.add_command(label='Add / Edit Notes',command=lambda:self._saved_notes(path))
        m.add_command(label='Compare With…',command=lambda:self.compare_dump_files(path))
        m.add_command(label='Write / Restore to Tag',command=lambda:self.restore_saved_dump(path))
        m.add_separator()
        m.add_command(label='Show Files / Open Folder',command=lambda:self._open_saved_folder(path))
        m.add_command(label='Export / Save As…',command=lambda:self._export_saved_backup(path))
        m.add_separator()
        m.add_command(label='Delete from Library…',command=lambda:self._delete_saved_backup(path))
        self._saved_popup_menu=m
        def dismiss_popup(ev=None):
            try:m.unpost()
            except Exception:pass
            try:self.unbind_all('<Button-1>')
            except Exception:pass
            try:self.unbind_all('<Escape>')
            except Exception:pass
        m.bind('<Unmap>',lambda e:self.after_idle(lambda:self.unbind_all('<Button-1>')))
        self.bind_all('<Button-1>',dismiss_popup,add='+'); self.bind_all('<Escape>',dismiss_popup,add='+')
        m.post(event.x_root,event.y_root)

    def _saved_dump_info(self,path):
        meta=self._saved_meta(path); files=self._saved_companions(path)
        tech=meta.get('technology') or 'Unknown / binary dump'; uid=meta.get('uid') or 'n/a'
        created=meta.get('created') or datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec='minutes')
        notes=meta.get('notes') or 'None'
        fl='\n'.join('• '+os.path.basename(x) for x in files if not x.endswith('.workshop.json'))
        messagebox.showinfo('Card / Dump Information',f'Name: {meta.get("display_name") or os.path.splitext(os.path.basename(path))[0]}\nTechnology: {tech}\nUID / ID: {uid}\nMemory dump: {os.path.getsize(path)} bytes\nSaved: {created}\n\nFiles:\n{fl}\n\nNotes: {notes}')

    def _saved_rename(self,path):
        meta=self._saved_meta(path); current=meta.get('display_name') or os.path.splitext(os.path.basename(path))[0]
        name=simpledialog.askstring('Rename Saved Card','Friendly name:',initialvalue=current,parent=self)
        if name is None:return
        name=name.strip()
        if not name:return
        meta['display_name']=name; self._save_saved_meta(path,meta); self.refresh_saved_library()

    def _saved_notes(self,path):
        meta=self._saved_meta(path)
        note=simpledialog.askstring('Card / Dump Notes','Notes for this saved card:',initialvalue=meta.get('notes',''),parent=self)
        if note is None:return
        meta['notes']=note.strip(); self._save_saved_meta(path,meta); self.refresh_saved_library()

    def _open_saved_folder(self,path):
        try:subprocess.Popen(['xdg-open',os.path.dirname(path)])
        except Exception as e:messagebox.showerror('Open Folder',str(e))

    def _export_saved_backup(self,path):
        dest=filedialog.askdirectory(title='Export Saved Card / Dump',initialdir=os.path.expanduser('~'),parent=self)
        if not dest:return
        copied=[]
        try:
            for src in self._saved_companions(path):
                target=os.path.join(dest,os.path.basename(src)); shutil.copy2(src,target); copied.append(target)
            messagebox.showinfo('Export Complete',f'Exported {len(copied)} file(s) to:\n{dest}')
        except Exception as e:messagebox.showerror('Export',str(e))

    def _delete_saved_backup(self,path):
        meta=self._saved_meta(path); name=meta.get('display_name') or os.path.basename(path)
        files=self._saved_companions(path)
        if not messagebox.askyesno('Delete Saved Card',f'Delete “{name}” and its {max(0,len(files)-1)} companion file(s) from the Workshop library?\n\nThis cannot be undone.',parent=self):return
        errors=[]
        for f in files:
            try:
                if os.path.isfile(f):os.remove(f)
            except OSError as e:errors.append(str(e))
        self.refresh_saved_library()
        if errors:messagebox.showwarning('Delete',f'The entry was refreshed, but some files could not be deleted:\n\n'+"\n".join(errors[:4]))

    def _page_action(self,name,fn):
        self._activity_feedback(name,'Opening '+name.lower()+'…',True,None); self.after(30,lambda:self._finish_page_action(name,fn))
    def _finish_page_action(self,name,fn):
        try:fn(); self._activity_feedback(name,name.title()+' opened.',False,True)
        except Exception as e:self._activity_feedback(name,str(e),False,False)

    def show_update_page(self):
        outer=self._simple_page('update','FIRMWARE / UPDATE','Check Workshop, RRG client and Proxmark3 firmware status from one place. Long operations show live progress below.')
        box=ttk.Frame(outer,style='Card2.TFrame',padding=14); box.pack(fill='x')
        ttk.Label(box,text='PROXMARK3 / RRG UPDATE',background=self.PANEL2,foreground=self.TEXT,font=('Sans',12,'bold')).pack(anchor='w')
        ttk.Label(box,text='Update checks and flashing use the existing verified updater. Flashing will clearly show DO NOT UNPLUG while active.',background=self.PANEL2,foreground=self.MUTED,wraplength=850).pack(anchor='w',pady=(4,10))
        ttk.Button(box,text='CHECK / OPEN UPDATE WORKFLOW',style='Primary.TButton',command=lambda:self._page_action('Firmware / Update',self.open_updater)).pack(anchor='w')

    def show_settings_page(self):
        outer=self._simple_page('settings','SETTINGS','Workshop behaviour and interface preferences.')
        box=ttk.Frame(outer,style='Card2.TFrame',padding=14); box.pack(fill='x')
        ttk.Label(box,text='CURRENT MODE',background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')).pack(anchor='w')
        ttk.Label(box,text='Beginner keeps specialist commands out of the way. Expert exposes technical controls.',background=self.PANEL2,foreground=self.MUTED).pack(anchor='w',pady=(4,8))
        ttk.Button(box,text='OPEN SETTINGS',style='Primary.TButton',command=lambda:self._page_action('Settings',self.open_settings)).pack(anchor='w')

    def show_help_page(self):
        outer=self._simple_page('help','HELP & INFO','Guides, supported technologies and Workshop information.')
        box=ttk.Frame(outer,style='Card2.TFrame',padding=14); box.pack(fill='x')
        ttk.Label(box,text=f'Proxmark3 Workshop v{VERSION}',background=self.PANEL2,foreground=self.TEXT,font=('Sans',12,'bold')).pack(anchor='w')
        ttk.Label(box,text='Use Home to scan. NFC work belongs in NFC Tag Studio. Dumps belong in Saved Cards & Dumps. Specialist utilities belong in Tools.',background=self.PANEL2,foreground=self.MUTED,wraplength=850).pack(anchor='w',pady=(4,10))
        ttk.Button(box,text='OPEN OFFLINE HELP',style='Primary.TButton',command=lambda:self._page_action('Help',self.show_offline_help)).pack(anchor='w')

    def toggle_console_size(self):
        """Expand/collapse the supporting output area without changing workflows."""
        self.console_expanded=not getattr(self,'console_expanded',False)
        self.console.config(height=18 if self.console_expanded else 4)

    def toggle_ui_mode(self):
        """Switch between a guided novice view and the full technical toolset."""
        cur=str(self.settings.get('ui_mode','Beginner')).lower()
        self.settings['ui_mode']='Expert' if cur!='expert' else 'Beginner'
        self._save_json(self.settings_path,self.settings)
        self._apply_ui_mode(rebuild=True)

    def _apply_ui_mode(self,rebuild=False):
        expert=str(self.settings.get('ui_mode','Beginner')).lower()=='expert'
        if hasattr(self,'mode_btn'):
            self.mode_btn.config(text='MODE: '+('EXPERT' if expert else 'BEGINNER'))
        if hasattr(self,'cmdbar'):
            try:
                if expert:
                    if not self.cmdbar.winfo_manager(): self.cmdbar.pack(fill='x',before=self.console,pady=(0,5))
                else: self.cmdbar.pack_forget()
            except Exception: pass
        # RF Lab is intentionally an Expert-only navigation destination.  It is
        # completely absent from Beginner mode rather than merely disabled.
        if hasattr(self,'rf_lab_btn'):
            try:
                if expert:
                    if not self.rf_lab_btn.winfo_manager():
                        before=self.nav_buttons.get('update')
                        self.rf_lab_btn.pack(fill='x',pady=2,before=before)
                else:
                    if getattr(self,'active_page',None)=='rflab': self.scanner_home()
                    self.rf_lab_btn.pack_forget()
            except Exception: pass
        if hasattr(self,'actions') and self.active_backend=='pm3': self._home_actions()

    def show_expert_console(self, owner=None):
        """Proxmark3 command console. It is deliberately not a Linux shell."""
        w=self._tool_window('Expert Proxmark3 Command Console','980x650'); w.transient(owner or self)
        outer=ttk.Frame(w,style='Card.TFrame',padding=14); outer.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(outer,text='EXPERT PROXMARK3 COMMAND CONSOLE',style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(outer,text='Run Proxmark3 client commands directly. This is a device console, not a Linux shell.',style='Muted.TLabel',wraplength=920).pack(anchor='w',pady=(3,8))
        out=tk.Text(outer,bg=self.CONSOLE,fg='#c9def6',insertbackground='white',selectbackground='#174a73',font=('Monospace',10),wrap='word',padx=10,pady=10); out.pack(fill='both',expand=True,pady=8)
        hist=[]; hpos=[0]; running=[False]
        row=ttk.Frame(outer,style='Card.TFrame'); row.pack(fill='x'); ttk.Label(row,text='pm3 >',style='Card.TLabel').pack(side='left')
        ent=ttk.Entry(row,font=('Monospace',10)); ent.pack(side='left',fill='x',expand=True,padx=8); runbtn=ttk.Button(row,text='RUN',style='Primary.TButton'); runbtn.pack(side='left')
        def append(t): out.insert('end',t); out.see('end')
        def run_cmd(event=None):
            cmd=ent.get().strip()
            if not cmd or running[0]: return 'break'
            if not self.connected: messagebox.showwarning('Expert Console','Connect the Proxmark3 first.',parent=w); return 'break'
            hist.append(cmd); hpos[0]=len(hist); append(f'\npm3 > {cmd}\n'); running[0]=True; runbtn.config(state='disabled')
            def worker():
                try:o=self.pm.execute(cmd); e=''
                except Exception as ex:o=''; e=str(ex)
                self.after(0,lambda:(append(ANSI_RE.sub('',(o or '')+('\nERROR: '+e if e else ''))+'\n'),running.__setitem__(0,False),runbtn.config(state='normal'),ent.focus_set()))
            threading.Thread(target=worker,daemon=True).start(); return 'break'
        def up(e=None):
            if hist: hpos[0]=max(0,hpos[0]-1); ent.delete(0,'end'); ent.insert(0,hist[hpos[0]])
            return 'break'
        def down(e=None):
            if hist: hpos[0]=min(len(hist),hpos[0]+1); ent.delete(0,'end'); ent.insert(0,hist[hpos[0]] if hpos[0]<len(hist) else '')
            return 'break'
        runbtn.config(command=run_cmd); ent.bind('<Return>',run_cmd); ent.bind('<Up>',up); ent.bind('<Down>',down)
        tools=ttk.Frame(outer,style='Card.TFrame'); tools.pack(fill='x',pady=(0,2)); ttk.Button(tools,text='COMMAND LIBRARY / BUILDER',style='Action.TButton',command=self.show_command_centre).pack(side='left'); ttk.Button(tools,text='CLEAR OUTPUT',style='Action.TButton',command=lambda:out.delete('1.0','end')).pack(side='right'); ent.focus_set()

    def _set_choice_state(self,kind,text,colour=None):
        # The Proxmark-only UI uses one compact connection button in the header.
        if not hasattr(self,'header_connect_btn'): return
        low=str(text).lower()
        if 'connected' in low and 'not connected' not in low:
            self.header_connect_btn.config(text='●  PROXMARK3 CONNECTED',bg=self.GREEN,fg='#03210d',activebackground='#32f17a',activeforeground='#03210d')
        elif 'connecting' in low:
            self.header_connect_btn.config(text='●  CONNECTING…',bg='#d99a25',fg='#071a2d',activebackground='#e7aa38',activeforeground='#071a2d')
        elif 'not found' in low:
            self.header_connect_btn.config(text='●  PROXMARK3 NOT FOUND',bg=self.RED,fg='white',activebackground='#ff707a',activeforeground='white')
        else:
            self.header_connect_btn.config(text='●  CONNECT PROXMARK3',bg=self.ACCENT,fg='white',activebackground='#269bff',activeforeground='white')

    def select_device(self,kind='pm3',connect=True):
        self.device_choice='pm3'; self.active_backend='pm3'
        if connect:self._connect_selected_device('pm3')

    def _connect_selected_device(self,kind='pm3'):
        if self.connected:
            self.connected=False; self._set_choice_state('pm3','● Not connected',self.MUTED); self.footer_status.config(text='●  Not connected')
            threading.Thread(target=self.pm.stop,daemon=True).start(); return
        ports=[p for p in self._serial_ports() if self._udev_label(p)!='chameleon']
        exact=[p for p in ports if self._udev_label(p)=='pm3']; candidates=exact or ([ports[0]] if len(ports)==1 else [])
        if not candidates:
            self._set_choice_state('pm3','● Device not found','#ffb347'); messagebox.showwarning(APP,'No Proxmark3 USB device was found.\n\nPlug it in and try again.'); return
        p=candidates[0]; self._set_choice_state('pm3','● Connecting…','#79bdff'); self.footer_status.config(text=f'●  Connecting to Proxmark3 on {p}…')
        threading.Thread(target=self._connect,args=(p,'pm3'),daemon=True).start()

    def _show_backend_ui(self, backend='pm3'):
        self._apply_ui_mode()



    def _set_device_art(self,model='generic',connected=False):
        name='proxmark3-rdv4.png' if 'rdv4' in str(model).lower() else 'proxmark3-generic.png'
        path=os.path.join(self.asset_dir,name)
        try:
            src=tk.PhotoImage(file=path)
            # Keep device artwork compact enough for a real 1366x768 desktop.
            factor=max(1, int(max(src.width()/175, src.height()/70)+0.999))
            self.device_photo=src.subsample(factor,factor) if factor>1 else src
            self.device_image.config(image=self.device_photo)
        except Exception:
            self.device_image.config(image='')
        if not connected:
            self.device_model.config(text='Proxmark3 Generic')
            self.device_state.config(text='●  Not connected',foreground=self.MUTED)
            if hasattr(self,'device_details'): self.device_details.config(text='Port: —   •   Firmware: —')

    def _update_device_card(self,raw):
        clean=ANSI_RE.sub('',raw or '')
        low=clean.lower()
        # Prefer the explicit platform line from `hw version`.  This avoids
        # mistaking generic hardware for RDV4 just because an FPGA/build string
        # happens to contain another platform reference.
        if 'platform name:' in low and 'generic target' in low:
            model='generic'; title='Proxmark3 Generic'
        elif 'platform name:' in low and ('rdv4' in low or 'rdv 4' in low):
            model='RDV4'; title='Proxmark3 RDV4'
        elif 'pm3generic' in low or 'proxmark3 generic target' in low:
            model='generic'; title='Proxmark3 Generic'
        elif 'pm3rdv4' in low or 'proxmark3 rdv4' in low:
            model='RDV4'; title='Proxmark3 RDV4'
        elif 'proxmark3' in low:
            model='generic'; title='Proxmark3 Generic'
        else:
            model='generic'; title='Proxmark3 Generic'
        self._set_device_art(model,connected=True)
        self.device_model.config(text=title)
        port=self.pm.port or 'Auto detected'
        self.device_state.config(text='●  Connected',foreground=self.GREEN)
        self.device_details.config(text=f'Port:       {port}\nFirmware:   connected\nClient:     RRG')
        self.footer_status.config(text=f'●  Connected to {title}   ({port})'); self._set_choice_state('pm3','● Connected',self.GREEN)

    def _home_actions(self):
        # Permanent scan controls: these never disappear after a result. Only their
        # colour/state changes; tag-specific actions are rendered underneath.
        for w in self.actions.winfo_children(): w.destroy()
        for w in self.scan_actions.winfo_children(): w.destroy()
        expert=str(self.settings.get('ui_mode','Beginner')).lower()=='expert'
        scanrow=ttk.Frame(self.scan_actions,style='Card.TFrame'); scanrow.pack(fill='x')
        self.home_lf_btn=ttk.Button(scanrow,text='SCAN LF  •  125 / 134 kHz',style='HomeAction.TButton',command=lambda:self._home_scan('lf search'))
        self.home_lf_btn.pack(side='left',fill='x',expand=True,padx=(0,3))
        self.home_hf_btn=ttk.Button(scanrow,text='SCAN HF  •  13.56 MHz',style='HomeAction.TButton',command=lambda:self._home_scan('hf search'))
        self.home_hf_btn.pack(side='left',fill='x',expand=True,padx=(3,0))
        ttk.Label(self.scan_actions,text='Place the tag on the correct antenna. Workshop will identify it and show the matching tools below.',style='Muted.TLabel',wraplength=900,justify='left').pack(anchor='w',pady=(5,0))
        self.dynamic_actions=ttk.Frame(self.actions,style='Card.TFrame'); self.dynamic_actions.pack(fill='both',expand=True)
        if expert:
            ttk.Separator(self.dynamic_actions).pack(fill='x',pady=6)
            ttk.Button(self.dynamic_actions,text='EXPERT COMMAND CONSOLE',style='Action.TButton',command=lambda:self.show_expert_console(self)).pack(fill='x',pady=3)
            ttk.Button(self.dynamic_actions,text='COMMAND LIBRARY',style='Action.TButton',command=self.show_command_centre).pack(fill='x',pady=3)
            ttk.Button(self.dynamic_actions,text='HARDWARE STATUS',style='Action.TButton',command=lambda:self.run('hw status')).pack(fill='x',pady=3)

    def _home_scan(self,cmd):
        try:
            self.home_lf_btn.config(style='Primary.TButton' if cmd=='lf search' else 'HomeAction.TButton')
            self.home_hf_btn.config(style='Primary.TButton' if cmd=='hf search' else 'HomeAction.TButton')
        except Exception: pass
        self.run(cmd)

    def _tool_window(self,title,size='820x620'):
        parent=getattr(self,'_tool_parent',None)
        if parent is None or not parent.winfo_exists(): parent=self
        w=tk.Toplevel(parent); w.title(title); w.configure(bg=self.BG); w.geometry(size); w.transient(parent)
        return w

    def _smart_child(self, workspace, callback):
        """Open a Workshop tool above Smart Tag Workspace, never behind it."""
        previous=getattr(self,'_tool_parent',None)
        self._tool_parent=workspace
        try: callback()
        finally: self._tool_parent=previous

    def _context_callback(self, owner, callback, *args, **kwargs):
        """Return a callback that keeps any new Workshop window owned by the window that launched it."""
        def wrapped():
            previous=getattr(self,'_tool_parent',None)
            self._tool_parent=owner
            try:
                return callback(*args, **kwargs)
            finally:
                self._tool_parent=previous
        return wrapped

    def _smart_ancestor(self, owner):
        """Find the Smart Tag Workspace owning a nested tool window, if any."""
        cur=owner
        while cur is not None and cur is not self:
            try:
                if getattr(cur,'_is_smart_workspace',False): return cur
                cur=cur.master
            except Exception:
                break
        return None

    def _context_run(self, owner, cmd):
        """Keep command output visible in the current Smart Workspace context."""
        smart=self._smart_ancestor(owner)
        if smart is not None:
            return self._smart_run(smart,cmd)
        return self.run(cmd)

    def _smart_run(self, workspace, cmd):
        """Run a PM3 command in a visible child result window and feed useful results back into Smart Workspace."""
        if not self.connected:
            messagebox.showwarning(APP,'Connect to the Proxmark3 first.',parent=workspace); return
        if self.busy:
            messagebox.showinfo(APP,'A Proxmark3 command is already running.',parent=workspace); return
        rw=tk.Toplevel(workspace); rw.title('Smart Workspace — '+self.friendly_operation(cmd)); rw.configure(bg=self.BG); rw.geometry('860x560'); rw.transient(workspace)
        f=ttk.Frame(rw,style='Card.TFrame',padding=12); f.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(f,text=self.friendly_operation(cmd).upper(),style='CardTitle.TLabel').pack(anchor='w')
        status=tk.StringVar(value='Running: '+cmd); ttk.Label(f,textvariable=status,style='Muted.TLabel').pack(anchor='w',pady=(3,8))
        out=tk.Text(f,bg=self.CONSOLE,fg='#c9def6',insertbackground='white',font=('Monospace',9),wrap='word',padx=10,pady=10); out.pack(fill='both',expand=True)
        bar=ttk.Frame(f,style='Card.TFrame'); bar.pack(fill='x',pady=(8,0))
        ttk.Button(bar,text='BACK TO TAG OVERVIEW',style='Action.TButton',command=rw.destroy).pack(side='left')
        def worker():
            try:
                result=self.pm.execute(cmd); err=None
                # T55xx can be physically present even when the first automatic
                # modulation pass cannot resolve Block 0.  In that case continue with
                # a READ-ONLY diagnostic ladder.  Never change lf t55xx config and never
                # write/wipe the tag merely to identify it.
                if cmd == 'lf t55xx detect' and ('could not detect modulation automatically' in result.lower() or 'try setting it manually' in result.lower()):
                    diagnostics=[]
                    for label,dcmd in (
                        ('PAGE 1 CHIP DETECTION','lf t55xx p1detect'),
                        ('BLOCK 0 READ','lf t55xx read -b 0'),
                        ('BLOCK 0 INTERPRETATION','lf t55xx info'),
                        ('TRACEABILITY','lf t55xx trace'),
                        ('RESET / RAW READ','lf t55xx resetread'),
                    ):
                        try:
                            dout=self.pm.execute(dcmd)
                            diagnostics.append((label,dcmd,dout))
                        except Exception as de:
                            diagnostics.append((label,dcmd,'[Workshop] '+str(de)))
                    result += '\n\n[Workshop] Automatic modulation detection was inconclusive.'
                    result += '\n[Workshop] Continuing with safe read-only T55xx diagnostics; no tag configuration was changed.\n'
                    for label,dcmd,dout in diagnostics:
                        result += '\n--- '+label+' ('+dcmd+') ---\n'+(dout or 'No output returned.')+'\n'
            except Exception as e: result=''; err=str(e)
            def finish():
                if not rw.winfo_exists(): return
                out.insert('1.0',result if result else ('Error: '+err if err else 'No output returned.')); out.config(state='disabled')
                status.set(('Completed: ' if not err else 'Failed: ')+cmd)
                if result:
                    self.raw_log += ('\n[Smart Workspace] '+cmd+'\n'+result+'\n')
                    if cmd in ('hf search','lf search','emv reader'):
                        tag=self.parse_tag(result)
                        if tag: self.last_tag=tag
                    if cmd == 'lf t55xx detect':
                        low=result.lower()
                        unresolved=('could not detect modulation automatically' in low or 'try setting it manually' in low)
                        # Keep the known family; never invent an exact T55xx model.
                        if self.last_tag and self.last_tag.get('kind')=='t55xx':
                            self.last_tag['name']='T55xx family' + (' — modulation unresolved' if unresolved else '')
                        fields=getattr(workspace,'_smart_fields',{})
                        if fields:
                            fields['title'].set(self.last_tag.get('name','T55xx family') if self.last_tag else 'T55xx family')
                            # Build a human-readable result from everything RRG actually
                            # established.  Even an unresolved modulation now reports the
                            # chipset evidence and which read-only probes produced data.
                            import re
                            clean_result=ANSI_RE.sub('',result)
                            low2=clean_result.lower()
                            evidence=[]
                            if 't55' in low2: evidence.append('Chipset: T55xx family')
                            if 'page 1 chip detection' in low2 and not re.search(r'(?i)(page 1|p1).*?(?:not detected|failed|could not)',clean_result): evidence.append('Page 1 probe: completed')
                            # Extract a real 8-hex-digit block value only when it occurs on
                            # a block/config data line; do not mistake timestamps/other IDs.
                            block0=''
                            for line in clean_result.splitlines():
                                ll=line.lower()
                                if ('blk' in ll or 'block' in ll or 'config' in ll) and re.search(r'(?i)\b[0-9a-f]{8}\b',line):
                                    mm=re.search(r'(?i)\b([0-9a-f]{8})\b',line)
                                    if mm:
                                        block0=mm.group(1).upper(); break
                            if block0: evidence.append('Block 0 / config candidate: '+block0)
                            # RRG's info/detect output may explicitly name these fields.
                            for label,pat in (
                                ('Modulation',r'(?im)^\s*(?:modulation|modulation mode)\s*[:.]?\s*([^\r\n]+)'),
                                ('Bit rate',r'(?im)^\s*(?:bit rate|bitrate|data rate)\s*[:.]?\s*([^\r\n]+)'),
                                ('Offset',r'(?im)^\s*(?:offset)\s*[:.]?\s*([^\r\n]+)'),
                            ):
                                mm=re.search(pat,clean_result)
                                if mm:
                                    val=mm.group(1).strip(' .|')
                                    if val and 'could not' not in val.lower(): evidence.append(label+': '+val[:80])
                            if unresolved:
                                fields['detect'].set('T55xx-family chip confirmed. Automatic modulation is unresolved, but Workshop completed the extended read-only diagnostic pass below.')
                                if not evidence: evidence=['T55xx-family response detected','No trustworthy modulation/config value could be decoded automatically']
                            else:
                                fields['detect'].set('T55xx detection completed and the workspace has been updated from the returned configuration.')
                            fields['detail'].set('  •  '.join(evidence[:6]))
                try: workspace.lift()
                except Exception: pass
            self.after(0,finish)
        threading.Thread(target=worker,daemon=True).start()

    def show_card_tools(self):
        if not self.last_tag:
            messagebox.showinfo('Card Tools','Scan a tag first. The toolbox changes automatically for the detected technology.'); return
        tag=self.last_tag; win=self._tool_window(f"Card Tools — {tag['name']}",'820x610')
        outer=ttk.Frame(win,style='Card.TFrame',padding=18); outer.pack(fill='both',expand=True,padx=10,pady=10)
        ttk.Label(outer,text='READ / COPY',style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(outer,text=tag['name'],style='TagTitle.TLabel').pack(anchor='w',pady=(5,1))
        ttk.Label(outer,text='Tools below are filtered for the detected technology. Write/restore operations still require confirmation.',style='Muted.TLabel',wraplength=760).pack(anchor='w',pady=(0,10))
        grid=ttk.Frame(outer,style='Card.TFrame'); grid.pack(fill='both',expand=True)
        menus={
          'mfc':[('Card Information','hf mf info'),('Backup Card','__backup__'),('NDEF Manager','__creator__'),('Read NDEF','hf mf ndefread'),('MAD Information','hf mf mad'),('Backup Library','__library__'),('Memory / Dump Viewer','__viewer__'),('Compare Backup Files','__compare__'),('Restore Backup','__restore__'),('Format as NFC / NDEF','__fmt_mfc__'),('Explain This Tag','__explain__'),('Scan Another Tag','__scan__')],
          'mfu':[('Tag Information','hf mfu info'),('Backup Tag','__backup__'),('NDEF Manager','__creator__'),('Read NDEF','hf mfu ndefread'),('Backup Library','__library__'),('Memory / Dump Viewer','__viewer__'),('Compare Backup Files','__compare__'),('Format as NFC / NDEF','__fmt_mfu__'),('Explain This Tag','__explain__'),('Scan Another Tag','__scan__')],
          'mfp':[('Card Information','hf mfp info'),('NDEF Manager','__creator__'),('Read NDEF','hf mfp ndefread'),('Format as NFC / NDEF','__fmt_mfp__'),('Backup Library','__library__'),('Memory / Dump Viewer','__viewer__'),('Compare Backup Files','__compare__'),('Explain This Tag','__explain__')],
          'type4a':[('ISO14443-A Information','hf 14a info'),('NDEF Manager','__creator__'),('Read NDEF','hf 14a ndefread'),('Format as NFC / NDEF','__fmt_type4a__'),('Backup Library','__library__'),('Compare Backup Files','__compare__'),('Explain This Tag','__explain__')],
          'desfire':[('Tag Information','hf mfdes info'),('Version / Type','hf mfdes getversion'),('Backup Library','__library__'),('Compare Backup Files','__compare__'),('Explain This Tag','__explain__')],
          'iso15693':[('Tag Information','hf 15 info'),('Backup Tag','__backup__'),('Reader','hf 15 reader'),('Backup Library','__library__'),('Memory / Dump Viewer','__viewer__'),('Compare Backup Files','__compare__'),('Explain This Tag','__explain__')],
          'iclass':[('Read / Identify','hf iclass reader'),('Backup (authorised)','__backup__'),('Backup Library','__library__'),('Memory / Dump Viewer','__viewer__'),('Compare Backup Files','__compare__'),('Explain This Tag','__explain__')],
          'hitag':[('Hitag Information','lf hitag info'),('Backup Library','__library__'),('Compare Backup Files','__compare__'),('Explain This Tag','__explain__')],
          'em410':[('Read / Identify','lf em 410x reader'),('Backup Library','__library__'),('Compare Backup Files','__compare__'),('Explain This Tag','__explain__')],
          't55xx':[('Detect / Configure','lf t55xx detect'),('Read Block 0','lf t55xx read -b 0'),('Backup Library','__library__'),('Compare Backup Files','__compare__'),('Explain This Tag','__explain__')],
          'em4x05':[('Tag Information','lf em 4x05 info'),('Dump / Backup','lf em 4x05 dump'),('Read Word','lf em 4x05 read'),('Backup Library','__library__'),('Explain This Tag','__explain__')],
          'cotag':[('Read / Identify','lf cotag reader'),('Explain This Tag','__explain__')],
          'ioprox':[('Read / Identify','lf io reader'),('Explain This Tag','__explain__')],
          'pyramid':[('Read / Identify','lf pyramid reader'),('Explain This Tag','__explain__')],
          'viking':[('Read / Identify','lf viking reader'),('Explain This Tag','__explain__')],
          'trovan':[('Read / Identify','lf trovan reader'),('Explain This Tag','__explain__')],
          'pcf7931':[('Read / Identify','lf pcf7931 reader'),('Explain This Tag','__explain__')],
          'pac':[('Read / Identify','lf pac reader'),('Explain This Tag','__explain__')],
          'destron':[('Read / Identify','lf destron reader'),('Explain This Tag','__explain__')],
          'ti':[('Read / Identify','lf ti reader'),('Explain This Tag','__explain__')],
          'gallagher':[('Read / Identify','lf gallagher reader'),('Explain This Tag','__explain__')],
          'idteck':[('Read / Identify','lf idteck reader'),('Explain This Tag','__explain__')],
          'motorola':[('Read / Identify','lf motorola reader'),('Explain This Tag','__explain__')],
          'nexwatch':[('Read / Identify','lf nexwatch reader'),('Explain This Tag','__explain__')],
          'noralsy':[('Read / Identify','lf noralsy reader'),('Explain This Tag','__explain__')],
          'legic':[('Tag Information','hf legic info'),('Backup Tag','__backup__'),('Reader','hf legic reader'),('Memory / Dump Viewer','__viewer__'),('Compare Backup Files','__compare__'),('Explain This Tag','__explain__')],
          'lto':[('Tag Information','hf lto info'),('Dump / Backup','hf lto dump'),('Reader','hf lto reader'),('Explain This Tag','__explain__')],
          'felica':[('Tag Information','hf felica info'),('Read Block','hf felica rdbl'),('Explain This Tag','__explain__')],
          'iso14443b':[('Tag Information','hf 14b info'),('Reader','hf 14b reader'),('Read NDEF','hf 14b ndefread'),('Dump / Backup','hf 14b dump'),('Memory / Dump Viewer','__viewer__'),('Explain This Tag','__explain__')],
          'ntag424':[('Tag Information','hf ntag424 info'),('Read File','hf ntag424 read'),('File Settings','hf ntag424 getfs'),('Explain This Tag','__explain__')],
          'seos':[('Tag Information','hf seos info'),('Read ADF','hf seos adf'),('Read GDF','hf seos gdf'),('Explain This Tag','__explain__')],
          'st25ta':[('Tag Information','hf st25ta info'),('Read NDEF','hf st25ta ndefread'),('Explain This Tag','__explain__')],
          'emv':[('Read EMV Information','emv reader'),('ISO14443-A Information','hf 14a info'),('Search Again','hf search'),('Technical Console','__console__')],
          'hf14a':[('ISO14443-A Information','hf 14a info'),('Search Again','hf search'),('Backup Library','__library__'),('Compare Backup Files','__compare__'),('Explain This Tag','__explain__')],
          'hidprox':[('Read / Identify','lf hid reader'),('Search Again','lf search'),('Explain This Tag','__explain__')],
          'awid':[('Read / Identify','lf awid reader'),('Search Again','lf search'),('Explain This Tag','__explain__')],
          'indala':[('Read / Identify','lf indala reader'),('Search Again','lf search'),('Explain This Tag','__explain__')],
          'fdxb':[('Read / Identify','lf fdxb reader'),('Search Again','lf search'),('Explain This Tag','__explain__')],
          'nedap':[('Read / Identify','lf nedap reader'),('Search Again','lf search'),('Explain This Tag','__explain__')],
          'paradox':[('Read / Identify','lf paradox reader'),('Search Again','lf search'),('Explain This Tag','__explain__')],
          'presco':[('Read / Identify','lf presco reader'),('Search Again','lf search'),('Explain This Tag','__explain__')],
          'securakey':[('Read / Identify','lf securakey reader'),('Search Again','lf search'),('Explain This Tag','__explain__')],
          'jablotron':[('Read / Identify','lf jablotron reader'),('Search Again','lf search'),('Explain This Tag','__explain__')],
          'keri':[('Read / Identify','lf keri reader'),('Search Again','lf search'),('Explain This Tag','__explain__')],
          'gproxii':[('Read / Identify','lf gproxii reader'),('Search Again','lf search'),('Explain This Tag','__explain__')],
          'unknown_lf':[('Re-scan Tag','__scan__'),('LF Search','lf search'),('Antenna Tune','hw tune'),('Recent Activity','__history__')]}
        def fire(cmd):
            if cmd=='__backup__': self.backup_current_tag()
            elif cmd=='__restore__': self.restore_backup()
            elif cmd=='__creator__': self.open_nfc_creator()
            elif cmd=='__explain__': self.explain_current_tag()
            elif cmd=='__library__': self.show_backup_manager()
            elif cmd=='__viewer__': self.open_dump_viewer()
            elif cmd=='__compare__': self.compare_dump_files()
            elif cmd=='__scan__': win.destroy(); self.run('auto')
            elif cmd=='__history__': self.show_history()
            elif cmd.startswith('__fmt_'):
                route={'__fmt_mfc__':'hf mf ndefformat','__fmt_mfu__':'hf mfu ndefformat','__fmt_mfp__':'hf mfp ndefformat','__fmt_type4a__':'hf 14a ndefformat'}[cmd]
                self.confirm_run('Format tag','Format this tag for NFC/NDEF? This changes tag data. Only continue with a tag you own or are authorised to program.',route)
            else:self.run(cmd)
        items=menus.get(tag['kind'])
        if items is None:
            items=list(self._tag_registry().get(tag['kind'],{}).get('actions',[]))
            if items: items.append(('Explain This Tag','__explain__'))
        for i,(label,cmd) in enumerate(items or []):
            ttk.Button(grid,text=label,style='Action.TButton',command=lambda c=cmd:fire(c)).grid(row=i//3,column=i%3,sticky='nsew',padx=4,pady=4,ipady=7)
        for c in range(3):grid.columnconfigure(c,weight=1)
        ttk.Separator(outer).pack(fill='x',pady=8)
        ttk.Label(outer,text='Protected credential/key-recovery attacks are intentionally not exposed as one-click workshop actions.',style='Muted.TLabel',wraplength=760).pack(anchor='w')

    def show_analysis(self):
        w=self._tool_window('Analysis & File Tools','900x650')
        outer=ttk.Frame(w,style='Card.TFrame',padding=16); outer.pack(fill='both',expand=True,padx=10,pady=10)
        ttk.Label(outer,text='ANALYSIS & FILE TOOLS',style='CardTitle.TLabel').pack(anchor='w')
        title=self.last_tag.get('name') if self.last_tag else 'No live tag selected'
        ttk.Label(outer,text=title,style='TagTitle.TLabel').pack(anchor='w',pady=(5,2))
        summary=ttk.Frame(outer,style='Card2.TFrame',padding=10); summary.pack(fill='x',pady=(4,10))
        if self.last_tag:
            t=self.last_tag; txt=f"Technology: {t.get('name')}    UID: {t.get('uid') or 'n/a'}    Family: {t.get('kind')}"
        else: txt='Scan a tag for live information, or analyse a saved dump without a tag connected.'
        ttk.Label(summary,text=txt,background=self.PANEL2,foreground=self.TEXT,wraplength=820).pack(anchor='w')
        grid=ttk.Frame(outer,style='Card.TFrame'); grid.pack(fill='x')
        actions=[('MEMORY / DUMP VIEWER',self.open_dump_viewer),('COMPARE TWO DUMPS',self.compare_dump_files),('BACKUP LIBRARY',self.show_backup_manager),('KEY DICTIONARIES',self.show_key_dictionary_manager),('COMMAND CENTRE',self.show_command_centre),('DEVICE DIAGNOSTICS',self.show_device_diagnostics),('OFFLINE HELP',self.show_offline_help),('RECENT ACTIVITY',self.show_history),('EXPLAIN DETECTED TAG',self.explain_current_tag),('TECHNICAL CONSOLE',lambda:(w.destroy(),self.set_console_mode(True)))]
        for i,(label,fn) in enumerate(actions):ttk.Button(grid,text=label,style='Action.TButton',command=fn).grid(row=i//2,column=i%2,sticky='ew',padx=4,pady=4,ipady=7)
        grid.columnconfigure(0,weight=1);grid.columnconfigure(1,weight=1)
        ttk.Label(outer,text='The dump viewer works offline with .bin/.mfd files and shows offsets, block/sector layout (where recognisable), hexadecimal data and ASCII.',style='Muted.TLabel',wraplength=830).pack(anchor='w',pady=(12,0))

    def _read_dump_bytes(self,path):
        if os.path.splitext(path)[1].lower()=='.json':
            try:
                obj=json.load(open(path,'r',encoding='utf-8'))
                if 't55' in str(obj.get('FileType','')).lower() and isinstance(obj.get('blocks'),dict):
                    vals=[]
                    for k in sorted(obj['blocks'],key=lambda x:int(x)):
                        v=str(obj['blocks'][k]).replace(' ','')
                        if re.fullmatch(r'[0-9A-Fa-f]{8}',v):vals.extend(bytes.fromhex(v))
                    if vals:return bytes(vals)
            except Exception:pass
        with open(path,'rb') as f:return f.read()

    def _workshop_test_write_kind(self,path):
        """Return a physical training route only for test files deliberately built for it.

        Never infer writability from file size.  Every writable supplied image is explicitly
        allow-listed here and has a technology-specific verification rule.
        """
        name=os.path.basename(path)
        return {
            'PLUK_MIFARE_Classic_1K_TEST.bin':'mfc',
            'PLUK_MIFARE_Classic_4K_TEST.bin':'mfc',
        }.get(name) if self._is_workshop_test_data(path) else None

    def _is_workshop_test_data(self,path):
        """True only for Workshop-supplied synthetic Test Data.

        Test Data is copied from the packaged test-dumps directory into the
        user-visible Test Dumps folder, so checking for a literal `test-dumps`
        path component is not sufficient.
        """
        try:
            rp=os.path.realpath(os.path.abspath(path))
            td=os.path.realpath(os.path.abspath(self.test_dump_dir))
            if os.path.commonpath([rp,td])==td:
                return True
        except (ValueError,OSError,AttributeError):
            pass
        bundled=os.path.join(os.path.dirname(os.path.abspath(__file__)),'test-dumps')
        try:
            return os.path.isfile(os.path.join(bundled,os.path.basename(path)))
        except Exception:
            return False

    def open_dump_viewer(self,path=None):
        if not path:
            path=filedialog.askopenfilename(title='Open Proxmark dump',initialdir=self.backup_dir,filetypes=[('Dump files','*.bin *.mfd'),('Binary files','*.bin'),('All files','*.*')])
        if not path:return
        try:data=self._read_dump_bytes(path)
        except Exception as e:messagebox.showerror('Dump Viewer',str(e));return
        w=self._tool_window('Memory / Dump Viewer — '+os.path.basename(path),'980x680')
        outer=ttk.Frame(w,style='Card.TFrame',padding=12);outer.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(outer,text='MEMORY / DUMP VIEWER',style='CardTitle.TLabel').pack(anchor='w')
        inferred=self._infer_external_dump(path); kind=inferred.get('technology') or ('MIFARE Classic 1K' if len(data)==1024 else ('MIFARE Classic 4K' if len(data)==4096 else 'Binary dump'))
        ttk.Label(outer,text=f'{os.path.basename(path)}  •  {len(data)} bytes  •  {kind}',style='TagTitle.TLabel').pack(anchor='w',pady=(4,8))
        pan=ttk.Panedwindow(outer,orient='horizontal');pan.pack(fill='both',expand=True)
        left=ttk.Frame(pan,style='Card2.TFrame',padding=8);right=ttk.Frame(pan,style='Card2.TFrame',padding=8);pan.add(left,weight=1);pan.add(right,weight=3)
        tree=ttk.Treeview(left,show='tree',selectmode='browse');tree.pack(fill='both',expand=True)
        text=tk.Text(right,bg=self.CONSOLE,fg='#c9def6',insertbackground='white',font=('Monospace',10),wrap='none',padx=8,pady=8);text.pack(fill='both',expand=True)
        blocks=[data[i:i+16] for i in range(0,len(data),16)]
        if len(data) in (1024,4096):
            for b,chunk in enumerate(blocks):
                if len(data)==1024: sector=b//4
                else: sector=b//4 if b<128 else 32+(b-128)//16
                sid=f's{sector}';
                if not tree.exists(sid):tree.insert('', 'end', iid=sid,text=f'Sector {sector}')
                tree.insert(sid,'end',iid=f'b{b}',text=f'Block {b:03d}')
        else:
            for b,_ in enumerate(blocks):tree.insert('','end',iid=f'b{b}',text=f'Block {b:03d}')
        def render_block(b=None):
            text.delete('1.0','end')
            seq=range(len(blocks)) if b is None else [b]
            for i in seq:
                chunk=blocks[i]; hx=' '.join(f'{x:02X}' for x in chunk); asc=''.join(chr(x) if 32<=x<127 else '.' for x in chunk)
                text.insert('end',f'{i*16:06X}  B{i:03d}  {hx:<47}  |{asc}|\n')
        def pick(_=None):
            sel=tree.selection()
            if sel and sel[0].startswith('b'):render_block(int(sel[0][1:]))
        tree.bind('<<TreeviewSelect>>',pick);render_block()
        bar=ttk.Frame(outer,style='Card.TFrame');bar.pack(fill='x',pady=(8,0))
        ttk.Button(bar,text='SHOW ALL BLOCKS',style='Action.TButton',command=render_block).pack(side='left')
        ttk.Button(bar,text='COMPARE WITH…',style='Action.TButton',command=self._context_callback(w,self.compare_dump_files,path)).pack(side='left',padx=5)
        test_write=self._workshop_test_write_kind(path)
        if test_write:
            ttk.Button(bar,text='PROGRAM TEST CARD • WRITE → READ BACK → VERIFY',style='Primary.TButton',command=lambda:self.restore_saved_dump(path,force_kind=test_write,allow_test=True)).pack(side='left',padx=5)
        elif self._is_workshop_test_data(path):
            ttk.Button(bar,text='OFFLINE / PROFILE TEST DATA',style='Action.TButton',state='disabled').pack(side='left',padx=5)
        else:
            ttk.Button(bar,text='WRITE / RESTORE TO TAG',style='Primary.TButton',command=lambda:self.restore_saved_dump(path)).pack(side='left',padx=5)
        ttk.Button(bar,text='OPEN ANOTHER…',style='Action.TButton',command=self._context_callback(w,self.open_dump_viewer)).pack(side='left')

    def restore_saved_dump(self,path,force_kind=None,allow_test=False):
        if self._is_workshop_test_data(path) and not (allow_test and self._workshop_test_write_kind(path)):
            messagebox.showinfo('Workshop Test Data','This supplied item is reference/offline test data and has no universal safe physical programming route.\n\nTo test a writable tag, scan the real tag and use that technology’s native RRG programming workflow (for example PROGRAM EM410X TEST / ID on T55xx).')
            return
        meta={}
        try:
            with open((path+'.workshop.json') if os.path.isfile(path+'.workshop.json') else (path+'.json'),'r',encoding='utf-8') as f: meta=json.load(f)
        except Exception: pass
        inferred=self._infer_external_dump(path)
        kind=force_kind or meta.get('kind','') or inferred.get('kind','')
        if not meta:meta=inferred
        routes={'mfc':'hf mf restore -f {f}','mfu':'hf mfu restore -f {f}','iso15693':'hf 15 restore -f {f}','iso14443b':'hf 14b restore -f {f}','legic':'hf legic restore -f {f}','lto':'hf lto restore -f {f}','t55xx':'lf t55xx restore -f {f}'}
        if kind not in routes:
            messagebox.showinfo('Write / Restore','This dump can be analysed offline, but Workshop does not yet have a verified guided restore route for this dump type. Expert mode can still use the RRG Command Centre where appropriate.')
            return
        if not self.connected:
            messagebox.showwarning('Write / Restore','Connect the Proxmark3 and place the compatible target tag/card on the correct antenna first.'); return
        expected=meta.get('technology') or kind
        # Never trust a previous Home-page scan for a write.  Re-detect the tag that is
        # physically on the antenna now, immediately before restore.  This also avoids
        # stale last_tag state when the dump viewer is a separate window.
        if self.busy:
            messagebox.showinfo('Write / Restore','Workshop is already running a Proxmark3 operation. Wait for it to finish and try again.'); return
        if not messagebox.askyesno('Check Target Before Restore',f'Backup: {os.path.basename(path)}\nType: {expected}\n\nKeep the target tag/card on the antenna. Workshop will detect it again now and confirm compatibility before anything is written.\n\nContinue?'):
            return
        self.busy=True
        self._activity_feedback('CHECKING RESTORE TARGET','Detecting the tag/card currently on the antenna — nothing is being written yet.',True,None)
        threading.Thread(target=self._detect_restore_target,args=(path,kind,meta,routes[kind]),daemon=True).start()

    def _detect_restore_target(self,path,kind,meta,route):
        try:
            scan_cmd='lf search' if kind=='t55xx' else 'hf search'
            out=self.pm.execute(scan_cmd)
            tag=self._canonical_tag_kind(self.refine_tag_identity(self.parse_tag(out),out))
            if kind=='t55xx' and tag and tag.get('kind')=='t55xx':
                try:
                    detail=self.pm.execute('lf t55xx detect')
                    out += '\n'+detail
                    reparsed=self._canonical_tag_kind(self.refine_tag_identity(self.parse_tag(out),out))
                    # The detail pass can contain generic diagnostic words. Never downgrade
                    # a positively detected T55xx target because of those extra lines.
                    if reparsed and reparsed.get('kind')=='t55xx': tag=reparsed
                except Exception:
                    pass
            self.q.put(('restore_target_result',(path,kind,meta,route,tag,out,None)))
        except Exception as e:
            self.q.put(('restore_target_result',(path,kind,meta,route,None,'',str(e))))

    def _finish_restore_target_check(self,payload):
        path,kind,meta,route,tag,out,error=payload
        self.busy=False
        if error:
            self._activity_feedback('CHECKING RESTORE TARGET','Target detection failed.',False,False)
            messagebox.showerror('Write / Restore',f'Workshop could not identify the target currently on the antenna.\n\n{error}'); return
        tag=self._canonical_tag_kind(tag)
        kind=self._canonical_tag_kind({'kind':kind}).get('kind',kind)
        actual=(tag or {}).get('kind','')
        compatible=(actual==kind)
        # Generic ISO14443-A identification is not sufficient proof for a Classic write.
        if not compatible:
            found=(tag or {}).get('name') or 'No compatible tag detected'
            self._activity_feedback('CHECKING RESTORE TARGET',f'Incompatible target: {found}',False,False)
            messagebox.showwarning('Incompatible Target',f'This backup expects: {meta.get("technology") or kind}\nDetected now: {found}\n\nNothing was written. Put the correct writable target on the antenna and press Write / Restore again.'); return
        self.last_tag=tag
        self._activity_feedback('TARGET VERIFIED',f'{tag.get("name",kind)} detected and compatible. Ready to restore.',False,True)
        if not messagebox.askyesno('Confirm Write / Restore',f'Target detected now: {tag.get("name",kind)}\nUID / ID: {tag.get("uid") or "n/a"}\n\nBackup: {os.path.basename(path)}\nSaved type: {meta.get("technology") or kind}\n\nCompatibility check passed. This next step WILL write to the target. Continue?'):
            return
        self._start_restore_write(path,kind,meta,route,tag)

    def _start_restore_write(self,path,kind,meta,route,tag):
        if self.busy:
            messagebox.showinfo('Write / Restore','Workshop is already running a Proxmark3 operation.'); return
        self.busy=True
        rw=self._tool_window('Write / Restore Progress','760x470')
        box=ttk.Frame(rw,style='Card.TFrame',padding=14); box.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(box,text='WRITE / RESTORE & VERIFY',style='CardTitle.TLabel').pack(anchor='w')
        status=tk.StringVar(value='● Preparing write…')
        detail=tk.StringVar(value='Target compatibility confirmed. Nothing has completed yet.')
        ttk.Label(box,textvariable=status,style='TagTitle.TLabel').pack(anchor='w',pady=(8,2))
        ttk.Label(box,textvariable=detail,style='Muted.TLabel',wraplength=700).pack(anchor='w',pady=(0,8))
        bar=ttk.Progressbar(box,mode='indeterminate'); bar.pack(fill='x',pady=(0,8)); bar.start(12)
        log=tk.Text(box,bg=self.CONSOLE,fg='#c9def6',font=('Monospace',9),wrap='word',height=12,padx=8,pady=8); log.pack(fill='both',expand=True)
        log.insert('end',f'Backup: {os.path.basename(path)}\nTarget: {tag.get("name",kind)}\n\n')
        rw._restore_status=status; rw._restore_detail=detail; rw._restore_bar=bar; rw._restore_log=log
        self._activity_feedback('WRITING TAG','Writing the selected dump to the verified target…',True,None)
        threading.Thread(target=self._restore_write_worker,args=(rw,path,kind,meta,route),daemon=True).start()

    def _mfc_sector_blocks(self,sector):
        """Return Classic block numbers for a sector (1K/4K geometry)."""
        if sector < 32:
            start=sector*4; return list(range(start,start+4))
        start=128+(sector-32)*16; return list(range(start,start+16))

    def _mfc_training_current_keys(self,total_blocks):
        """Use RRG autopwn to obtain CURRENT keys for an authorised training target.
        Returns {sector:(keyA,keyB)}, diagnostic output. No write is attempted here.
        """
        sectors=16 if total_blocks==64 else 40
        key_bytes=sectors*12
        sizeflag='--1k' if total_blocks==64 else '--4k'
        stamp='pluk-training-'+datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        before={}
        roots=[self.backup_dir,self.library_root,os.path.expanduser('~/.proxmark3'),os.path.expanduser('~/proxmark3')]
        for root in roots:
            if not os.path.isdir(root): continue
            for dp,dn,fn in os.walk(root):
                dn[:]=[d for d in dn if d not in ('.git','build','obj','armsrc','client','tools','fpga')]
                for n in fn:
                    if n.lower().endswith('.bin'):
                        q=os.path.join(dp,n)
                        try: before[q]=os.path.getmtime(q)
                        except OSError: pass
        with tempfile.TemporaryDirectory(prefix='pluk-mfc-prepare-') as td:
            cmd=f'hf mf autopwn {sizeflag} --suffix {stamp}'
            try: out=self.pm.execute(cmd,cwd=td) or ''
            except Exception as ex: return {},f'[COMMAND] {cmd}\n[Workshop] exception: {ex}\n'
            diag=f'[COMMAND] {cmd}\n{out}'
            candidates=[]
            # Prefer files created in our temporary working directory.
            for q in glob.glob(os.path.join(td,'**','*.bin'),recursive=True):
                try:
                    if os.path.getsize(q)==key_bytes:candidates.append(q)
                except OSError: pass
            # RRG savepaths can override cwd. Parse paths it prints and also look
            # for newly-created key-sized BIN files in its small known save roots.
            clean=ANSI_RE.sub('',out)
            for raw in re.findall(r'(?im)(?:keys?.*?(?:dumped|saved).*?to|saved.*?file)\s+[`"\']?([^`"\'\r\n]+\.bin)',clean):
                q=os.path.abspath(os.path.expanduser(raw.strip().rstrip(' .')))
                try:
                    if os.path.isfile(q) and os.path.getsize(q)==key_bytes:candidates.append(q)
                except OSError: pass
            for root in roots:
                if not os.path.isdir(root): continue
                for dp,dn,fn in os.walk(root):
                    dn[:]=[d for d in dn if d not in ('.git','build','obj','armsrc','client','tools','fpga')]
                    for n in fn:
                        if not n.lower().endswith('.bin'): continue
                        q=os.path.join(dp,n)
                        try:
                            if os.path.getsize(q)==key_bytes and (q not in before or os.path.getmtime(q)>before[q]+0.01): candidates.append(q)
                        except OSError: pass
            if not candidates:
                return {},diag+'\n[Workshop] RRG did not produce a complete Classic key file; programming was not started.\n'
            q=max(set(candidates),key=lambda x:os.path.getmtime(x))
            try: raw=open(q,'rb').read()
            except OSError as ex: return {},diag+f'\n[Workshop] Could not read recovered key file: {ex}\n'
            if len(raw)!=key_bytes:return {},diag+f'\n[Workshop] Recovered key file has unexpected size {len(raw)} (expected {key_bytes}).\n'
            keys={}
            # RRG Classic key files are KeyA table followed by KeyB table.
            a=raw[:sectors*6]; b=raw[sectors*6:]
            for sec in range(sectors):
                ka=a[sec*6:(sec+1)*6].hex().upper(); kb=b[sec*6:(sec+1)*6].hex().upper()
                if len(ka)==12 and len(kb)==12: keys[sec]=(ka,kb)
            diag+=f'\n[Workshop] Prepared {len(keys)}/{sectors} sector key pairs from RRG key recovery/check.\n'
            return keys,diag

    def _restore_write_worker(self,rw,path,kind,meta,route):
        try:
            self.q.put(('restore_write_stage',(rw,'● Writing tag…','RRG is writing the training data. Keep the tag on the antenna.')))
            write_out=''
            # Workshop MIFARE Classic training images are deliberately programmed block-by-block.
            # A normal genuine Classic has an immutable manufacturer block 0, so preserve it.
            # The supplied training images use the transport key FFFFFFFFFFFF and valid trailers.
            if kind=='mfc' and self._workshop_test_write_kind(path)=='mfc':
                source=self._read_dump_bytes(path)
                if len(source) not in (1024,4096):
                    raise RuntimeError(f'Training image has an invalid MIFARE Classic size ({len(source)} bytes).')
                total=len(source)//16
                # IMPORTANT: the keys inside the training image are the DESTINATION
                # keys.  They must never be assumed to be the keys currently on the
                # physical card.  Recover/check the target card's current sector keys
                # first using RRG's native authorised Classic workflow.
                self.q.put(('restore_write_stage',(rw,'● Preparing MIFARE Classic target…','Checking/recovering the CURRENT sector keys before any write is attempted.')))
                current_keys,key_out=self._mfc_training_current_keys(total)
                write_out += '\n--- PREPARE / CURRENT KEYS ---\n'+key_out
                sectors=16 if total==64 else 40
                missing=[i for i in range(sectors) if i not in current_keys]
                if missing:
                    self.q.put(('restore_write_result',(rw,path,kind,write_out,'',False,'',f'Target preparation stopped before writing. Workshop could not obtain a usable current key for sector(s): {", ".join(map(str,missing))}. Nothing was written to those sectors.')))
                    return
                failures=[]
                # Program one sector at a time: data blocks first, trailer last.
                # This keeps the CURRENT authentication key valid until the sector's
                # final operation changes its trailer to the known training keys.
                for sec in range(sectors):
                    blocks=self._mfc_sector_blocks(sec)
                    ordered=[b for b in blocks if b!=blocks[-1]]+[blocks[-1]]
                    ka,kb=current_keys[sec]
                    for b in ordered:
                        if b==0: continue  # genuine Classic manufacturer block is immutable
                        data=source[b*16:(b+1)*16].hex().upper()
                        self.q.put(('restore_write_stage',(rw,f'● Sector {sec+1}/{sectors} • block {b}…',f'Writing with the CURRENT key recovered from this physical card. Manufacturer block 0 is preserved.')))
                        o=''; used=None
                        # Access conditions can require Key A or Key B. Try the known
                        # current A first, then current B when different.
                        for kt,key in (('A',ka),('B',kb)):
                            if not key or (kt=='B' and key==ka): continue
                            cmd=f'hf mf wrbl --blk {b} -{kt.lower()} -k {key} -d {data}'
                            try: attempt=self.pm.execute(cmd) or ''
                            except Exception as ex: attempt=f'[Workshop] exception: {ex}\n'
                            o += f'[COMMAND] {cmd}\n{attempt}'
                            low=ANSI_RE.sub('',attempt).lower()
                            bad=any(x in low for x in ('auth error','write ( fail','error','failed','could not','no tag found','timeout','isok:00','isok: 00'))
                            if not bad:
                                used=kt; break
                        write_out += f'\n--- SECTOR {sec} BLOCK {b} ---\n{o}'
                        if used is None:
                            failures.append(b)
                            # Do not hammer the rest of this sector after auth/access
                            # has already failed. Move on so the result stays readable.
                            break
                if failures:
                    self.q.put(('restore_write_result',(rw,path,kind,write_out,'',False,'',f'MIFARE Classic training write stopped on block(s): {", ".join(map(str,failures))}. Current keys were checked first; see the captured RRG output for the exact access/authentication result.')))
                    return
            else:
                cmd=route.format(f=self._quote_pm3(path))
                try:
                    write_out=self.pm.execute(cmd) or ''
                except Exception as ex:
                    write_out=f'[COMMAND] {cmd}\n[Workshop] exception: {ex}\n'
                    self.q.put(('restore_write_result',(rw,path,kind,write_out,'',False,'','RRG could not complete the write/restore command. See the captured command and error below.')))
                    return
                low=ANSI_RE.sub('',write_out).lower()
                if any(x in low for x in ('error','failed','could not','no tag found','timeout')):
                    self.q.put(('restore_write_result',(rw,path,kind,write_out,'',False,'','RRG reported a write/restore error. See the captured output below.')))
                    return
            self.q.put(('restore_write_stage',(rw,'● Reading tag back…','Write stage finished. Workshop is now reading the target back for verification.')))
            verify_out=''; verified=False; summary=''
            if kind=='t55xx':
                expected={}
                try:
                    obj=json.load(open(path,'r',encoding='utf-8')) if path.lower().endswith('.json') else {}
                    expected={int(k):str(v).replace(' ','').upper() for k,v in obj.get('blocks',{}).items() if re.fullmatch(r'[0-9A-Fa-f]{8}',str(v).replace(' ',''))}
                except Exception: expected={}
                reads={}
                # Page 0 blocks are the normal writable credential/config area.
                for b in sorted(k for k in expected if 0 <= k <= 7):
                    ro=self.pm.execute(f'lf t55xx read -b {b}') or ''
                    verify_out += f'\n--- READ BACK BLOCK {b} ---\n'+ro
                    vals=re.findall(r'(?i)\b([0-9a-f]{8})\b',ANSI_RE.sub('',ro))
                    if vals: reads[b]=vals[-1].upper()
                if expected:
                    checked=[b for b in expected if 0 <= b <= 7]
                    mism=[b for b in checked if reads.get(b)!=expected[b]]
                    verified=bool(checked) and not mism and all(b in reads for b in checked)
                    summary=('All writable test blocks matched the dump.' if verified else 'Read-back mismatch/missing blocks: '+', '.join(map(str,mism or [b for b in checked if b not in reads])))
                else:
                    det=self.pm.execute('lf t55xx detect') or ''; verify_out+='\n--- POST-WRITE DETECT ---\n'+det
                    verified=bool(det.strip()) and 'could not detect' not in det.lower()
                    summary='T55xx re-detected after restore.' if verified else 'T55xx could not be verified after restore.'
            elif kind=='mfc' and self._workshop_test_write_kind(path)=='mfc':
                # Training verification is deliberately file-free.  A test write is not a
                # backup operation: read every programmed DATA block straight back from the
                # physical card and compare the 16 returned bytes with the training image.
                # Sector trailers are not byte-compared because MIFARE Classic deliberately
                # masks key material when a trailer is read.  Instead, successful reads using
                # the destination/training sector key prove the new authentication state.
                source=self._read_dump_bytes(path)
                total=len(source)//16
                sectors=16 if total==64 else 40
                checked=[]; mismatches=[]; unreadable=[]
                def _read_block_bytes(text, block):
                    clean=ANSI_RE.sub('',text or '')
                    # Current RRG prefixes table rows with its log marker, e.g.
                    #   [=]  62 | 50 4C ... 20 | PLUK ...
                    # Older output may omit the marker.  Do not anchor the parser at
                    # column zero; find the requested block row and extract exactly
                    # the 16 hex bytes between the table separators.
                    for line in clean.splitlines():
                        m=re.search(r'(?<![0-9])'+str(block)+r'\s*\|\s*((?:[0-9A-Fa-f]{2}(?:\s+|$)){16})', line)
                        if m:
                            vals=re.findall(r'(?i)\b[0-9a-f]{2}\b',m.group(1))
                            if len(vals)==16:
                                try:return bytes.fromhex(''.join(vals))
                                except ValueError:pass
                    # Compatibility with older client output: isOk:01 data:...
                    m=re.search(r'(?is)isOk\s*:\s*01.*?data\s*:\s*((?:[0-9A-Fa-f]{2}\s*){16})',clean)
                    if m:
                        vals=re.findall(r'(?i)\b[0-9a-f]{2}\b',m.group(1))
                        if len(vals)==16:
                            try:return bytes.fromhex(''.join(vals))
                            except ValueError:pass
                    return None
                for sec in range(sectors):
                    blocks=self._mfc_sector_blocks(sec)
                    trailer=blocks[-1]
                    tr=source[trailer*16:(trailer+1)*16]
                    keya=tr[:6].hex().upper(); keyb=tr[10:16].hex().upper()
                    data_blocks=[b for b in blocks if b!=trailer and b!=0]
                    for b in data_blocks:
                        expected=source[b*16:(b+1)*16]
                        got=None; attempts=''
                        for kt,key in (('A',keya),('B',keyb)):
                            if not key or (kt=='B' and key==keya):continue
                            cmd=f'hf mf rdbl --blk {b} -{kt.lower()} -k {key}'
                            try:ro=self.pm.execute(cmd) or ''
                            except Exception as ex:ro=f'[Workshop] exception: {ex}\n'
                            attempts += f'[COMMAND] {cmd}\n{ro}'
                            got=_read_block_bytes(ro,b)
                            if got is not None:break
                        verify_out += f'\n--- VERIFY SECTOR {sec} BLOCK {b} ---\n{attempts}'
                        if got is None:
                            unreadable.append(b)
                        elif got != expected:
                            mismatches.append(b)
                            verify_out += f'[Workshop] EXPECTED: {expected.hex().upper()}\n[Workshop] READBACK: {got.hex().upper()}\n'
                        else:
                            checked.append(b)
                            verify_out += '[Workshop] VERIFIED: exact 16-byte match\n'
                verified=not unreadable and not mismatches and bool(checked)
                if verified:
                    summary=f'Test card programmed and verified directly: {len(checked)}/{len(checked)} writable data blocks matched. Manufacturer block 0 preserved. Verification used direct physical read-back; no backup/dump was requested.'
                else:
                    parts=[]
                    if mismatches:parts.append('mismatch block(s): '+', '.join(map(str,mismatches)))
                    if unreadable:parts.append('could not read block(s): '+', '.join(map(str,unreadable)))
                    summary=f'Direct read-back verified {len(checked)} data block(s); '+('; '.join(parts) if parts else 'verification was incomplete')+'. Verification used direct physical block reads.'
            else:
                # Other technologies retain their existing temporary dump verification route.
                dumpcmd={'mfu':'hf mfu dump -f verify_readback','iso15693':'hf 15 dump -f verify_readback','iso14443b':'hf 14b dump -f verify_readback','legic':'hf legic dump -f verify_readback','lto':'hf lto dump -f verify_readback'}.get(kind)
                if not dumpcmd:
                    raise RuntimeError('No verified read-back route exists for this technology.')
                with tempfile.TemporaryDirectory(prefix='pm3-workshop-verify-') as td:
                    verify_out=self.pm.execute(dumpcmd,cwd=td) or ''
                    candidates=[]
                    for pat in ('verify_readback','verify_readback.bin','verify_readback.dump','verify_readback.json','verify_readback.eml'):
                        q=os.path.join(td,pat)
                        if os.path.isfile(q): candidates.append(q)
                    candidates += [q for q in glob.glob(os.path.join(td,'verify_readback*')) if os.path.isfile(q) and q not in candidates]
                    if not candidates:
                        raise RuntimeError('Read-back command finished but no verification dump was created.')
                    source=self._read_dump_bytes(path)
                    readback=self._read_dump_bytes(candidates[0])
                    if kind=='mfc' and self._workshop_test_write_kind(path)=='mfc':
                        # Manufacturer block 0 is immutable on normal genuine MIFARE Classic cards.
                        # Training verification therefore compares the writable memory only.
                        source_cmp=source[16:]; readback_cmp=readback[16:16+len(source_cmp)]
                        verified=(len(readback)>=len(source) and source_cmp==readback_cmp)
                        if verified:
                            summary=f'Training image verified: all writable blocks matched ({len(source_cmp)//16} blocks). Manufacturer block 0 was preserved.'
                        else:
                            common=min(len(source_cmp),len(readback_cmp))
                            diffs=sum(1 for i in range(common) if source_cmp[i]!=readback_cmp[i])+abs(len(source_cmp)-len(readback_cmp))
                            summary=f'Training image read-back mismatch in writable memory ({diffs} differing byte(s)). Block 0 is intentionally ignored.'
                    else:
                        verified=(source==readback)
                        if verified:
                            summary=f'Read-back matched the source dump byte-for-byte ({len(source)} bytes).'
                        else:
                            common=min(len(source),len(readback))
                            diffs=sum(1 for i in range(common) if source[i]!=readback[i])+abs(len(source)-len(readback))
                            summary=f'Read-back does not match the source dump ({diffs} differing byte(s); source {len(source)} B, read-back {len(readback)} B).'
            self.q.put(('restore_write_result',(rw,path,kind,write_out,verify_out,verified,summary,None)))
        except Exception as e:
            self.q.put(('restore_write_result',(rw,path,kind,locals().get('write_out',''),locals().get('verify_out',''),False,'',str(e))))

    def _finish_restore_write(self,payload):
        rw,path,kind,write_out,verify_out,verified,summary,error=payload
        self.busy=False
        try:
            if not rw.winfo_exists(): return
            rw._restore_bar.stop()
            rw._restore_log.insert('end','--- WRITE OUTPUT ---\n'+(write_out or '(no output)')+'\n\n--- VERIFY OUTPUT ---\n'+(verify_out or '(no output)')+'\n')
            rw._restore_log.see('end')
            if error:
                rw._restore_status.set('✗ WRITE / VERIFY FAILED'); rw._restore_detail.set(error)
                self._activity_feedback('WRITE / VERIFY FAILED',error,False,False)
                messagebox.showerror('Write / Restore Failed',error,parent=rw)
            elif verified:
                rw._restore_status.set('✓ WRITE & VERIFY COMPLETE'); rw._restore_detail.set(summary)
                self._activity_feedback('WRITE & VERIFY COMPLETE',summary,False,True)
                messagebox.showinfo('Write / Restore Complete','Write completed and the read-back verification passed.\n\n'+summary,parent=rw)
                # Refresh Home from the physical tag so encoded LF ID/format is visible.
                if kind=='t55xx': self.after(150,lambda:self.run('lf search'))
            else:
                rw._restore_status.set('⚠ WRITE FINISHED — VERIFY FAILED'); rw._restore_detail.set(summary or 'Read-back did not match.')
                self._activity_feedback('VERIFY FAILED',summary,False,False)
                messagebox.showwarning('Verify Failed','The write command finished, but Workshop could not verify the result.\n\n'+summary,parent=rw)
        except Exception:
            pass

    def compare_dump_files(self,first=None):
        if not first:first=filedialog.askopenfilename(title='Select first dump',initialdir=self.backup_dir,filetypes=[('Dump files','*.bin *.mfd'),('All files','*.*')])
        if not first:return
        second=filedialog.askopenfilename(title='Select second dump',initialdir=self.backup_dir,filetypes=[('Dump files','*.bin *.mfd'),('All files','*.*')])
        if not second:return
        try:a=self._read_dump_bytes(first);b=self._read_dump_bytes(second)
        except Exception as e:messagebox.showerror('Compare Dumps',str(e));return
        w=self._tool_window('Compare Dumps','900x620');outer=ttk.Frame(w,style='Card.TFrame',padding=12);outer.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(outer,text='DUMP COMPARISON',style='CardTitle.TLabel').pack(anchor='w')
        common=min(len(a),len(b));diff=[i for i in range(common) if a[i]!=b[i]]; extra=abs(len(a)-len(b))
        ttk.Label(outer,text=f'{os.path.basename(first)} ({len(a)} bytes)  ↔  {os.path.basename(second)} ({len(b)} bytes)',style='TagTitle.TLabel',wraplength=840).pack(anchor='w',pady=(4,3))
        ttk.Label(outer,text=f'{len(diff)+extra} differing byte(s). {common-len(diff)} byte(s) identical in the shared range.',style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        t=tk.Text(outer,bg=self.CONSOLE,fg='#c9def6',font=('Monospace',10),wrap='none',padx=8,pady=8);t.pack(fill='both',expand=True)
        if not diff and not extra:t.insert('end','✓ Files are byte-for-byte identical.\n')
        else:
            t.insert('end','OFFSET   FILE A  FILE B   BLOCK\n');t.insert('end','-'*38+'\n')
            for i in diff[:500]:t.insert('end',f'{i:06X}     {a[i]:02X}      {b[i]:02X}      {i//16}\n')
            if len(diff)>500:t.insert('end',f'\n… {len(diff)-500} more differing positions not shown.\n')
            if extra:t.insert('end',f'\nFile lengths differ by {extra} byte(s).\n')
        t.config(state='disabled')

    def show_backup_manager(self):
        w=self._tool_window('Backup Library','900x600');outer=ttk.Frame(w,style='Card.TFrame',padding=12);outer.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(outer,text='BACKUP LIBRARY',style='CardTitle.TLabel').pack(anchor='w');ttk.Label(outer,text=self.backup_dir,style='Muted.TLabel').pack(anchor='w',pady=(2,8))
        cols=('type','uid','source','size','date');tree=ttk.Treeview(outer,columns=cols,show='tree headings',selectmode='browse');tree.heading('#0',text='Backup file')
        for c,h,wid in [('type','Technology',190),('uid','UID',150),('size','Size',80),('date','Modified',150)]:tree.heading(c,text=h);tree.column(c,width=wid,anchor='w')
        tree.column('#0',width=260);tree.pack(fill='both',expand=True); paths={}
        files=sorted(glob.glob(os.path.join(self.backup_dir,'**','*.bin'),recursive=True)+glob.glob(os.path.join(self.backup_dir,'**','*.mfd'),recursive=True),key=lambda x:os.path.getmtime(x),reverse=True)
        for n,path in enumerate(files):
            meta={}
            try:
                with open(path+'.json','r',encoding='utf-8') as f:meta=json.load(f)
            except Exception:pass
            iid=f'f{n}';paths[iid]=path;tree.insert('', 'end',iid=iid,text=os.path.basename(path),values=(meta.get('technology',''),meta.get('uid',''),os.path.getsize(path),datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M')))
        bar=ttk.Frame(outer,style='Card.TFrame');bar.pack(fill='x',pady=(8,0))
        def chosen():
            s=tree.selection();return paths.get(s[0]) if s else None
        ttk.Button(bar,text='VIEW DUMP',style='Action.TButton',command=lambda:self._context_callback(w,self.open_dump_viewer,chosen())() if chosen() else None).pack(side='left')
        ttk.Button(bar,text='COMPARE WITH…',style='Action.TButton',command=lambda:self._context_callback(w,self.compare_dump_files,chosen())() if chosen() else None).pack(side='left',padx=5)
        ttk.Button(bar,text='OPEN BACKUP FOLDER',style='Action.TButton',command=lambda:subprocess.Popen(['xdg-open',self.backup_dir])).pack(side='left')
        ttk.Label(bar,text=f'{len(files)} backup file(s)',style='Muted.TLabel').pack(side='right')

    def show_utilities(self):
        """Open the single Tools page inside the permanent application workspace."""
        self._clear_right_page(); self._select_page('tools'); self._activity_feedback('Tools','Tools page ready.',False,None)
        old=getattr(self,'tools_page',None)
        if old is not None:
            try:
                if old.winfo_exists(): old.destroy()
            except Exception: pass
        page=ttk.Frame(self.main_body,style='Card.TFrame'); self.tools_page=page; page.pack(fill='both',expand=True)
        # Keep the Tools page compact enough for the Workshop's 1366x768 target
        # without shrinking the readable fonts.  The page uses tighter vertical
        # spacing and shorter buttons; three-column grouping remains consistent.
        outer=ttk.Frame(page,style='Card.TFrame',padding=(14,8)); outer.pack(fill='both',expand=True)
        head=ttk.Frame(outer,style='Card.TFrame'); head.pack(fill='x')
        ttk.Label(head,text='TOOLS',style='TagTitle.TLabel',font=('Sans',16,'bold')).pack(side='left')
        ttk.Label(head,text='All workshop utilities in one place',style='Muted.TLabel').pack(side='left',padx=12)
        ttk.Label(outer,text='Device checks, card utilities, dictionaries and diagnostics.',style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(2,6))
        sections=[
          ('DEVICE & SCANNING',[('HARDWARE VERSION',lambda:self.run('hw version')),('HARDWARE STATUS',lambda:self.run('hw status')),('ANTENNA TUNE',lambda:self.run('hw tune')),('HF SEARCH',lambda:self.run('hf search')),('LF SEARCH',lambda:self.run('lf search')),('DEVICE DIAGNOSTICS',self.show_device_diagnostics)]),
          ('CARD / TAG TOOLS',[('BACKUP CURRENT TAG',self.backup_current_tag),('AUTHENTICATION / KEYS',self.show_authentication_tools),('SMART TAG WORKSPACE',self.show_smart_tag_workspace),('COMPARE TAG A / B',self.compare_tags),('JOB / TAG NOTES',self.edit_tag_notes)]),
          ('WORKSHOP UTILITIES',[('KEY DICTIONARIES',self.show_key_dictionary_manager),('COMMAND CENTRE',self.show_command_centre),('SAVE CONSOLE LOG',self.save_log),('COPY DIAGNOSTICS',self.copy_diagnostics)]),
        ]
        for title,items in sections:
            box=ttk.Frame(outer,style='Card2.TFrame',padding=(8,6)); box.pack(fill='x',pady=3)
            ttk.Label(box,text=title,background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')).pack(anchor='w',pady=(0,1))
            grid=ttk.Frame(box,style='Card2.TFrame'); grid.pack(fill='x')
            for i,(label,fn) in enumerate(items):
                ttk.Button(grid,text=label,style='Action.TButton',command=fn).grid(row=i//3,column=i%3,sticky='ew',padx=3,pady=2,ipady=2)
            for c in range(3): grid.columnconfigure(c,weight=1)

    def show_rf_lab(self):
        """Expert-only RF analysis/security workspace inside the main window."""
        if str(self.settings.get('ui_mode','Beginner')).lower()!='expert':
            self.scanner_home(); return
        self._clear_right_page(); self._select_page('rflab'); self._activity_feedback('RF Lab','Expert RF workspace ready.',False,None)
        page=ttk.Frame(self.main_body,style='Card.TFrame'); self.rf_lab_page=page; page.pack(fill='both',expand=True)
        outer=ttk.Frame(page,style='Card.TFrame',padding=(14,8)); outer.pack(fill='both',expand=True)
        head=ttk.Frame(outer,style='Card.TFrame'); head.pack(fill='x')
        ttk.Label(head,text='RF LAB',style='TagTitle.TLabel',font=('Sans',16,'bold')).pack(side='left')
        ttk.Label(head,text='Expert RF analysis & authorised security testing',style='Muted.TLabel').pack(side='left',padx=12)
        ttk.Label(outer,text='Technical Proxmark3 tools. Commands here can require protocol knowledge; writing/destructive operations remain behind their normal confirmations.',style='Muted.TLabel',wraplength=930).pack(anchor='w',pady=(2,6))

        tag=self.last_tag or {}
        ctx=ttk.Frame(outer,style='Card2.TFrame',padding=(8,6)); ctx.pack(fill='x',pady=3)
        ttk.Label(ctx,text='LIVE CONTEXT',background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')).pack(anchor='w')
        if tag:
            ttk.Label(ctx,text=f"Detected: {tag.get('name','Unknown')}   •   Family: {tag.get('kind','unknown')}   •   UID: {tag.get('uid') or 'n/a'}",background=self.PANEL2,foreground=self.FG).pack(anchor='w',pady=(2,0))
        else:
            ttk.Label(ctx,text='No tag selected — use Home to scan first, or use the RF/raw analysis tools below.',background=self.PANEL2,foreground=self.MUTED).pack(anchor='w',pady=(2,0))

        sections=[
          ('RF ANALYSIS',[('ANTENNA TUNE',lambda:self.run('hw tune')),('DATA HELP',lambda:self.run('data help')),('SAMPLE BUFFER',lambda:self.run('data samples')),('DETECT CLOCK',lambda:self.run('data detectclock')),('AUTOCORRELATION',lambda:self.run('data autocorr')),('PLOT SAMPLES',lambda:self.run('data plot'))]),
          ('CAPTURE & PROTOCOL',[('HF COMMANDS',lambda:self.run('hf help')),('LF COMMANDS',lambda:self.run('lf help')),('TRACE LIST',lambda:self.run('trace list')),('HF SEARCH',lambda:self.run('hf search')),('LF SEARCH',lambda:self.run('lf search')),('SMART TAG WORKSPACE',self.show_smart_tag_workspace)]),
          ('AUTHENTICATION & SECURITY TESTING',[('AUTH / KEY TOOLS',self.show_authentication_tools),('KEY DICTIONARIES',self.show_key_dictionary_manager),('PROTOCOL COMMANDS',self.show_command_centre),('EXPERT PM3 CONSOLE',self.show_expert_console)]),
        ]
        for title,items in sections:
            box=ttk.Frame(outer,style='Card2.TFrame',padding=(8,6)); box.pack(fill='x',pady=3)
            ttk.Label(box,text=title,background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')).pack(anchor='w',pady=(0,1))
            grid=ttk.Frame(box,style='Card2.TFrame'); grid.pack(fill='x')
            for i,(label,fn) in enumerate(items):
                ttk.Button(grid,text=label,style='Action.TButton',command=fn).grid(row=i//3,column=i%3,sticky='ew',padx=3,pady=2,ipady=2)
            for c in range(3): grid.columnconfigure(c,weight=1)

        note=ttk.Frame(outer,style='Card2.TFrame',padding=(8,6)); note.pack(fill='x',pady=3)
        ttk.Label(note,text='EXPERT NOTE',background=self.PANEL2,foreground='#ffca68',font=('Sans',9,'bold')).pack(anchor='w')
        ttk.Label(note,text='RF Lab exposes analysis and technical entry points, not automatic credential attacks. Use protocol-specific RRG commands only on systems you own or are authorised to test.',background=self.PANEL2,foreground=self.MUTED,wraplength=920).pack(anchor='w',pady=(2,0))

    # ---- Key Dictionary Manager -------------------------------------------------
    # Workshop never edits RRG supplied dictionaries.  Imported authorised/default
    # key lists are normalised, de-duplicated and accumulated in My Keys.
    def _key_specs(self):
        return {
            'hf_mifare_classic': ('HF 13.56 MHz', 'MIFARE Classic', 12, 'mifare-classic.dic'),
            'hf_ntag_ultralight': ('HF 13.56 MHz', 'Ultralight / NTAG', None, 'ntag-ultralight.dic'),
            'hf_other': ('HF 13.56 MHz', 'Other HF', None, 'other-hf.dic'),
            'lf_hitag': ('LF 125 / 134 kHz', 'Hitag', None, 'hitag.dic'),
            'lf_other': ('LF 125 / 134 kHz', 'Other LF', None, 'other-lf.dic'),
        }
    def _dictionary_files(self):
        roots=[]
        pmroot=os.path.expanduser('~/proxmark3')
        for rel in ('client/dictionaries','client/resources','dictionaries','resources'):
            d=os.path.join(pmroot,rel)
            if os.path.isdir(d): roots.append(d)
        supplied=[]
        for root in roots:
            for ext in ('*.dic','*.txt'):
                supplied += glob.glob(os.path.join(root,'**',ext),recursive=True)
        custom=glob.glob(os.path.join(self.my_keys_dir,'*.dic'))
        return sorted(set(supplied)), sorted(set(custom))
    def _normalise_key_lines(self,path, expected_len=None):
        keys=[]; invalid=0
        try:
            raw=open(path,'r',encoding='utf-8',errors='ignore').read().splitlines()
        except Exception:
            return [],0
        for line in raw:
            line=line.split('#',1)[0].split(';',1)[0].strip()
            if not line: continue
            token=re.sub(r'[^0-9A-Fa-f]','',line).upper()
            # Dictionary credentials are represented as complete hex tokens only.
            if not token or len(token)%2 or not re.fullmatch(r'[0-9A-F]+',token): invalid+=1; continue
            if expected_len and len(token)!=expected_len: invalid+=1; continue
            if len(token)<4 or len(token)>64: invalid+=1; continue
            keys.append(token)
        return keys,invalid
    def _all_installed_keys(self):
        supplied,custom=self._dictionary_files(); out=set()
        for p in supplied+custom:
            ks,_=self._normalise_key_lines(p); out.update(ks)
        return out
    def show_key_dictionary_manager(self):
        w=self._tool_window('Key Dictionaries','960x650'); outer=ttk.Frame(w,style='Card.TFrame',padding=14); outer.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(outer,text='KEY DICTIONARIES',style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(outer,text='Installed RRG packages + one cumulative My Keys store',style='TagTitle.TLabel').pack(anchor='w',pady=(4,2))
        ttk.Label(outer,text='RRG dictionaries stay untouched. Add Dictionary compares every valid entry with installed keys and saves only genuinely new entries into My Keys.',style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(0,9))
        cols=('source','group','entries','path'); tree=ttk.Treeview(outer,columns=cols,show='tree headings',height=14)
        tree.heading('#0',text='Dictionary'); tree.column('#0',width=220)
        for c,h,wid in [('source','Source',90),('group','Type / frequency',190),('entries','Entries',80),('path','Location',330)]: tree.heading(c,text=h); tree.column(c,width=wid,anchor='w')
        tree.pack(fill='both',expand=True)
        status=ttk.Label(outer,text='',style='Muted.TLabel'); status.pack(anchor='w',pady=(6,0))
        def refresh():
            tree.delete(*tree.get_children()); supplied,custom=self._dictionary_files(); total=0
            for i,p in enumerate(supplied+custom):
                ks,_=self._normalise_key_lines(p); total+=len(set(ks)); src='My Keys' if p in custom else 'RRG'
                low=os.path.basename(p).lower(); group='HF/LF • supplied'
                if 'mifare' in low or 'mf_' in low: group='HF • MIFARE'
                elif 'hitag' in low: group='LF • Hitag'
                tree.insert('', 'end', text=os.path.basename(p), values=(src,group,len(set(ks)),p))
            status.config(text=f'{len(supplied)} RRG package(s) • {len(custom)} My Keys file(s) • {len(self._all_installed_keys())} unique installed key values')
        bar=ttk.Frame(outer,style='Card.TFrame'); bar.pack(fill='x',pady=(8,0))
        ttk.Button(bar,text='ADD / UPDATE MY KEYS',style='Primary.TButton',command=lambda:self.import_key_dictionary(refresh)).pack(side='left')
        ttk.Button(bar,text='BACKUP MY KEYS',style='Action.TButton',command=self.backup_my_keys).pack(side='left',padx=5)
        ttk.Button(bar,text='RESTORE MY KEYS',style='Action.TButton',command=lambda:self.restore_my_keys(refresh)).pack(side='left')
        ttk.Button(bar,text='REFRESH',style='Action.TButton',command=refresh).pack(side='right')
        refresh()
    def import_key_dictionary(self, refresh_cb=None):
        path=filedialog.askopenfilename(title='Add Dictionary',filetypes=[('Dictionary files','*.dic *.txt'),('All files','*.*')])
        if not path:return
        specs=self._key_specs(); choices=[f'{v[0]} — {v[1]}' for v in specs.values()]
        pick=simpledialog.askstring('Dictionary type','Choose the destination type by entering a number:\n\n'+'\n'.join(f'{i+1}. {x}' for i,x in enumerate(choices))+'\n\nUse the technology the dictionary was made for.',parent=self)
        try: idx=int(pick)-1; key=list(specs)[idx]
        except Exception:
            if pick is not None: messagebox.showwarning('Add Dictionary','No valid dictionary type was selected.')
            return
        freq,label,expected_len,filename=specs[key]
        incoming,invalid=self._normalise_key_lines(path,expected_len)
        if not incoming:
            messagebox.showwarning('Add Dictionary','No valid key entries were found for the selected dictionary type.'); return
        unique_in=list(dict.fromkeys(incoming)); dup_in=len(incoming)-len(unique_in)
        installed=self._all_installed_keys(); new=[k for k in unique_in if k not in installed]
        dest=os.path.join(self.my_keys_dir,filename); existing,_=self._normalise_key_lines(dest,expected_len) if os.path.exists(dest) else ([],0)
        merged=list(dict.fromkeys(existing+new))
        if new:
            with open(dest,'w',encoding='utf-8') as f:
                f.write('# Proxmark3 Workshop - My Keys\n# '+freq+' / '+label+'\n'); f.write('\n'.join(merged)+'\n')
        hist=self._load_json(self.key_history_path,[]); hist.append({'date':datetime.datetime.now().isoformat(timespec='seconds'),'source':os.path.basename(path),'type':key,'found':len(incoming),'unique':len(unique_in),'new':len(new),'already_installed':len(unique_in)-len(new),'duplicates_in_file':dup_in,'invalid':invalid}); self._save_json(self.key_history_path,hist[-500:])
        messagebox.showinfo('Dictionary Import Complete',f'File: {os.path.basename(path)}\nType: {freq} — {label}\n\nKeys found: {len(incoming)}\nAlready installed: {len(unique_in)-len(new)}\nDuplicates within file: {dup_in}\nNew keys added: {len(new)} / {len(unique_in)}\nInvalid entries: {invalid}\n\nMy Keys now contains {len(merged)} key(s) for this type.')
        if refresh_cb:refresh_cb()
    def backup_my_keys(self):
        stamp=datetime.datetime.now().strftime('%Y-%m-%d')
        path=filedialog.asksaveasfilename(title='Backup My Keys',defaultextension='.zip',initialfile=f'Proxmark3-Workshop-My-Keys-{stamp}.zip',filetypes=[('ZIP backup','*.zip')])
        if not path:return
        manifest={'format':'Proxmark3 Workshop My Keys','version':1,'created':datetime.datetime.now().isoformat(timespec='seconds'),'workshop_version':VERSION}
        try:
            with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
                for f in glob.glob(os.path.join(self.my_keys_dir,'*.dic')): z.write(f,'my-keys/'+os.path.basename(f))
                if os.path.exists(self.key_history_path): z.write(self.key_history_path,'import-history.json')
                z.writestr('manifest.json',json.dumps(manifest,indent=2))
            messagebox.showinfo('Backup My Keys',f'Backup created successfully.\n\n{path}')
        except Exception as e: messagebox.showerror('Backup My Keys',str(e))
    def restore_my_keys(self, refresh_cb=None):
        path=filedialog.askopenfilename(title='Restore My Keys',filetypes=[('Workshop key backup','*.zip')])
        if not path:return
        try:
            with zipfile.ZipFile(path,'r') as z:
                names=z.namelist()
                if 'manifest.json' not in names: raise ValueError('This is not a recognised Workshop My Keys backup.')
                manifest=json.loads(z.read('manifest.json').decode('utf-8'))
                if manifest.get('format')!='Proxmark3 Workshop My Keys': raise ValueError('Unsupported backup format.')
                installed=self._all_installed_keys(); added=0; checked=0
                specs=self._key_specs()
                by_filename={v[3]:(k,v) for k,v in specs.items()}
                for name in names:
                    base=os.path.basename(name)
                    if not name.startswith('my-keys/') or base not in by_filename: continue
                    _,spec=by_filename[base]; expected=spec[2]
                    with tempfile.NamedTemporaryFile('wb',delete=False) as t: t.write(z.read(name)); tmp=t.name
                    incoming,_=self._normalise_key_lines(tmp,expected); os.unlink(tmp); checked+=len(set(incoming))
                    dest=os.path.join(self.my_keys_dir,base); old,_=self._normalise_key_lines(dest,expected) if os.path.exists(dest) else ([],0)
                    fresh=[k for k in dict.fromkeys(incoming) if k not in installed]; installed.update(fresh); added+=len(fresh)
                    merged=list(dict.fromkeys(old+fresh))
                    if fresh:
                        with open(dest,'w',encoding='utf-8') as f:f.write('# Proxmark3 Workshop - My Keys\n# Restored / merged\n'+'\n'.join(merged)+'\n')
            messagebox.showinfo('Restore My Keys',f'Backup checked: {checked} unique entries\nNew keys restored: {added}\nExisting keys were kept; nothing was blindly overwritten.')
            if refresh_cb:refresh_cb()
        except Exception as e:messagebox.showerror('Restore My Keys',str(e))

    def _udev_label(self,port):
        """Classify a serial device from Linux metadata only; never open the port here."""
        try:
            cp=subprocess.run(['udevadm','info','-q','property','-n',port],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=2)
            props={}
            for line in cp.stdout.splitlines():
                if '=' in line:
                    k,v=line.split('=',1); props[k]=v
            # Include all useful descriptive fields and symlink names. Newer Chameleon
            # firmware exposes a USB serial/product string; older builds may only expose
            # Nordic/nRF metadata, so do not call generic Nordic devices Chameleons.
            keys=('ID_VENDOR','ID_VENDOR_FROM_DATABASE','ID_MODEL','ID_MODEL_FROM_DATABASE','ID_SERIAL','ID_SERIAL_SHORT','DEVLINKS','ID_PATH')
            blob=' '.join(props.get(k,'') for k in keys).lower()
            if 'chameleon' in blob or 'chameleonultra' in blob: return 'chameleon'
            if 'proxmark' in blob or '/dev/pm3-' in blob: return 'pm3'
        except Exception: pass
        try:
            rp=os.path.realpath(port)
            for link in glob.glob('/dev/serial/by-id/*'):
                if os.path.realpath(link)==rp:
                    name=os.path.basename(link).lower()
                    if 'chameleon' in name:return 'chameleon'
                    if 'proxmark' in name:return 'pm3'
        except Exception: pass
        return 'unknown'
    def _serial_ports(self):
        return sorted(set(glob.glob('/dev/pm3-*')+glob.glob('/dev/ttyACM*')+glob.glob('/dev/ttyUSB*')))
    def refresh_ports(self):
        ps=self._serial_ports(); vals=['Auto detect Proxmark3']; self.detected_devices=[]
        for p in ps:
            kind=self._udev_label(p)
            if kind=='chameleon': continue
            vals.append(f'Proxmark3 — {p}'); self.detected_devices.append(('pm3',p,'USB'))
        self.port['values']=vals
        if self.port.get() not in vals: self.port.set('Auto detect Proxmark3')

    def _selection(self):
        v=self.port.get().strip(); m=re.search(r'(/dev/[^ ]+)$',v)
        return ('pm3',m.group(1)) if m else ('pm3',None)

    def toggle_connect(self):
        self._connect_selected_device(self.device_choice or 'pm3')

    def _connect(self,p,kind='pm3'):
        try:
            self.pm.configure(p); self.q.put(('log',f'[GUI] Proxmark3 USB: {p}\n')); out=self.pm.execute('hw version',18)
            if 'Proxmark3' not in out and 'Client' not in out: raise RuntimeError('The selected device did not return a Proxmark3 response.')
            self.active_backend='pm3'; self.q.put(('connected',out)); self.q.put(('details',self.parse_version(out)))
        except Exception as e:self.q.put(('error',str(e)))

    def _parse_cu_reader_result(self, text, mode='hf'):
        clean=ANSI_RE.sub('',text or '').replace('\r','')
        result={'mode':mode,'title':'No tag detected','family':'—','uid':'—','atqa':'—','sak':'—','ats':'—','raw':clean}
        if re.search(r'(?i)no .*tag|tag.*no|not found',clean): return result
        if mode=='lf':
            m=re.search(r'(?im)(?:ID|EM410X[^\n]*?)\s*[:=]\s*([0-9A-F][0-9A-F :.-]{4,})',clean)
            if m:
                ident=re.sub(r'[^0-9A-Fa-f]','',m.group(1)).upper(); result.update(title='LF tag detected',family='EM410X / 125 kHz',uid=ident)
            return result
        def grab(name):
            m=re.search(r'(?im)^\s*[-•>]*\s*'+re.escape(name)+r'\s*[:=]\s*([^\n]+)',clean)
            return m.group(1).strip() if m else '—'
        uid=grab('UID'); atqa=grab('ATQA'); sak=grab('SAK'); ats=grab('ATS')
        if uid!='—':
            fam='ISO14443-A'
            sakhex=re.sub(r'[^0-9A-Fa-f]','',sak).upper()[:2]
            if sakhex=='08': fam='MIFARE Classic 1K / ISO14443-A'
            elif sakhex=='18': fam='MIFARE Classic 4K / ISO14443-A'
            elif sakhex=='09': fam='MIFARE Mini / ISO14443-A'
            elif sakhex in ('00','04'): fam='ISO14443-A / Ultralight-or-NTAG class'
            result.update(title='HF tag detected',family=fam,uid=uid,atqa=atqa,sak=sak,ats=ats)
        return result







    def parse_version(self,s):
        clean=ANSI_RE.sub('',s)
        for line in clean.splitlines():
            t=line.strip()
            if ('Device' in t or 'Target' in t or 'Client' in t) and len(t)<110:return t
        return 'Proxmark3 connected'
    def run(self,cmd):
        if not self.connected: messagebox.showwarning(APP,'Connect the Proxmark3 first.'); return
        if self.busy: messagebox.showinfo(APP,'A Proxmark3 command is already running.'); return
        self.busy=True; self.current_cmd=cmd; self.current_op=self.friendly_operation(cmd); self.status.config(text='●  WORKING…'); self.activity.config(text=self.current_op+'…'); self.scan_btn.config(state='disabled'); getattr(self,'scan_hf_btn',self.scan_btn).config(state='disabled'); self.stop_btn.config(state='normal'); self.start_operation_progress(self.current_op,cmd); self._activity_feedback(self.current_op,'Proxmark3 command running — watch this bar or Technical Details for live output.',True,None);
        if cmd in ('auto','hf search','lf search'): self.clear_console(); self.tag_title.config(text='Scanning…'); self.tag_meta.config(text=('Scanning LF 125 / 134 kHz and identifying the exact tag/chip where possible.' if cmd=='lf search' else 'Scanning HF 13.56 MHz and identifying the exact card/tag chip where possible.'));
        elif not self.raw_mode: self.show_clean_operation(self.current_op)
        threading.Thread(target=self._run,args=(cmd,),daemon=True).start()
    def _run(self,cmd):
        start=len(self.raw_log)
        try:
            # GUI Auto Scan deliberately does NOT call RRG `auto`: that command can
            # fall through to an LF trace capture before we get a chance to probe EMV.
            # Instead use staged, non-writing detection and only fall back to LF last.
            if cmd == 'auto':
                self.q.put(('log','[GUI] Stage 1/3 — searching HF technologies…\n'))
                hf_out=self.pm.execute('hf search')
                hf_tag=self.parse_tag(hf_out)
                scan_out=hf_out

                # Payment cards can be missed by the broad HF search even though
                # `emv reader` activates them successfully, so probe EMV independently
                # unless a specific non-payment HF family is already positively known.
                specific_hf = hf_tag and hf_tag.get('kind') in ('mfc','mfu','mfp','desfire','iso15693','iclass')
                if not specific_hf:
                    try:
                        self.q.put(('log','\n[GUI] Stage 2/3 — checking ISO14443/EMV payment card…\n'))
                        emv_out=self.pm.execute('emv reader')
                        emv_tag=self.parse_tag(emv_out)
                        scan_out=hf_out+'\n'+emv_out
                        if emv_tag and emv_tag.get('kind')=='emv':
                            self.q.put(('scan_result',scan_out)); self.q.put(('idle',None)); return
                    except Exception as emv_err:
                        self.q.put(('log',f'[GUI] EMV probe did not identify a card: {emv_err}\n'))

                if hf_tag:
                    self.q.put(('scan_result',scan_out)); self.q.put(('idle',None)); return

                self.q.put(('log','\n[GUI] Stage 3/3 — searching LF technologies…\n'))
                lf_out=self.pm.execute('lf search')
                scan_out += '\n'+lf_out
                self.q.put(('scan_result',scan_out))
            else:
                backup_ctx=getattr(self,'pending_backup',None)
                backup_cwd=backup_ctx.get('cwd') if backup_ctx and ((' dump ' in (' '+cmd+' ')) or cmd.startswith('hf mf autopwn')) else None
                if backup_cwd:
                    self.q.put(('log',f'[GUI] Backup working directory: {backup_cwd}\n'))
                out=self.pm.execute(cmd,cwd=backup_cwd)
                if cmd in ('hf search','lf search'):
                    scan_out=out
                    first=self.parse_tag(out)
                    # RRG LF search can report a special-case chipset (notably T55xx)
                    # after saying no known encoded LF protocol was found.  That is still
                    # a real detection.  Automatically run the family detector so the GUI
                    # finishes the identification instead of remaining on 'Scanning...'.
                    if cmd == 'lf search' and first and first.get('kind') == 't55xx':
                        try:
                            self.q.put(('log','\n[GUI] T55xx-family chipset detected — checking configuration…\n'))
                            detail_out=self.pm.execute('lf t55xx detect')
                            scan_out=out+'\n'+detail_out
                            # A T55xx/T5577 is the physical chip.  Also decode the
                            # credential it is currently emulating so Home tells the
                            # user what is actually programmed on the tag.
                            try:
                                em_out=self.pm.execute('lf em 410x reader')
                                if em_out and not any(x in em_out.lower() for x in ('no tag found','no em410','timeout')):
                                    scan_out += '\n\n--- ENCODED LF CREDENTIAL ---\n'+em_out
                            except Exception as em_err:
                                self.q.put(('log',f'[GUI] Encoded LF credential probe skipped: {em_err}\n'))
                        except Exception as detail_err:
                            self.q.put(('log',f'[GUI] T55xx detail pass could not complete: {detail_err}\n'))
                    if cmd != 'lf search' and first and first.get('kind') in ('hf14a','type4a'):
                        try:
                            self.q.put(('log','\n[GUI] ISO14443 smart card detected — checking for EMV application…\n'))
                            emv_out=self.pm.execute('emv reader')
                            emv_tag=self.parse_tag(emv_out)
                            if emv_tag and emv_tag.get('kind')=='emv': scan_out=out+'\n'+emv_out
                        except Exception as emv_err:
                            self.q.put(('log',f'[GUI] EMV identification pass skipped: {emv_err}\n'))
                    self.q.put(('scan_result',scan_out))
                else:
                    self.q.put(('command_result',(cmd,out)))
            self.q.put(('idle',None))
        except Exception as e:
            self.q.put(('command_error',(cmd,str(e),self.raw_log[start:])))
    def start_operation_progress(self,title,cmd=''):
        if getattr(self,'update_mode',False): return
        self.op_active=True; self.op_started=time.monotonic(); self.op_last_activity=self.op_started
        title = 'READING TAG' if cmd in ('auto','hf search','lf search') else title.upper()
        self.op_title.config(text=title); self.op_detail.config(text='Starting Proxmark3 operation…'); self.op_elapsed.config(text='Elapsed 00:00')
        self.op_panel.pack(fill='x',before=self.console); self.op_bar.start(12); self._tick_operation_progress()
    def _tick_operation_progress(self):
        if not self.op_active:return
        elapsed=int(time.monotonic()-self.op_started); idle=int(time.monotonic()-self.op_last_activity)
        self.op_elapsed.config(text=f'Elapsed {elapsed//60:02d}:{elapsed%60:02d}  •  Last activity {idle}s ago')
        if idle>=12:self.op_detail.config(text=f'Still working / waiting for the tag — no new output for {idle}s')
        self.after(1000,self._tick_operation_progress)
    def finish_operation_progress(self,message=None,success=True):
        if not self.op_active:return
        self.op_active=False; self.op_bar.stop()
        if message:
            self.op_detail.config(text=('✓ ' if success else '✗ ')+message)
            self.after(1800,lambda:self.op_panel.pack_forget() if not self.op_active else None)
        else:self.op_panel.pack_forget()
    def _touch_operation_progress(self,line=''):
        if not self.op_active:return
        self.op_last_activity=time.monotonic()
        clean=ANSI_RE.sub('',line).strip()
        if clean:
            # Keep the friendly strip concise; full output remains in console.
            self.op_detail.config(text=clean[:150]); self.op_stage.config(text='✓ Proxmark3 connected    ● Working with tag    ○ Reading card information    ○ Loading tools')
    def _safe_filename(self,text):
        return re.sub(r'[^A-Za-z0-9._-]+','_',text or 'tag').strip('_') or 'tag'
    def _backup_search_roots(self):
        """Locations RRG may use for dump output.

        Newer RRG clients can honour their configured savepaths instead of the
        process cwd.  Keep this deliberately small and user-local, then copy
        only files created during the current operation into Workshop's chosen
        library.
        """
        roots=[self.backup_dir, os.path.expanduser('~/.proxmark3'), os.path.expanduser('~/proxmark3')]
        out=[]
        for r in roots:
            r=os.path.abspath(os.path.expanduser(r))
            if os.path.isdir(r) and r not in out: out.append(r)
        return out
    def _snapshot_backup_files(self):
        snap=set()
        for root in self._backup_search_roots():
            try:
                for dp,dn,fn in os.walk(root):
                    # Source/build trees can be huge.  RRG saves are normally in
                    # the root or shallow user data folders; don't crawl git/build.
                    dn[:]=[d for d in dn if d not in ('.git','build','obj','armsrc','client','tools','fpga')]
                    for n in fn:
                        if n.lower().endswith(('.bin','.json','.eml','.mfd','.dump')):
                            snap.add(os.path.join(dp,n))
            except OSError: pass
        return snap
    def backup_current_tag(self):
        if not self.last_tag:
            messagebox.showwarning('Backup','Scan the tag first so the backup can be matched to the correct technology.'); return
        if self.busy:return
        kind=self.last_tag.get('kind'); uid=self._safe_filename((self.last_tag.get('uid') or 'unknown').replace(' ',''))
        routes={'mfu':'hf mfu dump -f {f}','mfp':'hf mfp dump -f {f}','desfire':'hf mfdes dump -f {f}','iso15693':'hf 15 dump -f {f}','iso14443b':'hf 14b dump -f {f}','iclass':'hf iclass dump -f {f}','legic':'hf legic dump -f {f}','lto':'hf lto dump -f {f}','topaz':'hf topaz dump -f {f}','xerox':'hf xerox dump -f {f}','fudan':'hf fudan dump -f {f}','em4x05':'lf em 4x05 dump -f {f}','em4x50':'lf em 4x50 dump -f {f}','t55xx':'lf t55xx dump -f {f}','hitag':'lf hitag dump -f {f}','hitaghtu':'lf hitag htu dump -f {f}'}
        if kind!='mfc' and kind not in routes:
            messagebox.showinfo('Backup','A native saveable dump is not enabled for this detected tag family.'); return
        friendly=simpledialog.askstring('Name This Dump','Give this backup a friendly name:',initialvalue=f'{self.last_tag.get("name","Tag")} {uid}')
        if friendly is None:return
        friendly=friendly.strip() or f'{self.last_tag.get("name","Tag")} {uid}'
        stamp=datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S'); stem=f'{self._safe_filename(friendly)}_{stamp}'; base=os.path.join(self.backup_dir,stem)
        before=self._snapshot_backup_files()
        if kind=='mfc':
            # A Classic `dump` needs a complete key file first.  `autopwn` is
            # RRG's supported automatic authorised backup workflow: recover/check
            # sector keys, then save key + card dump files.  --1k matches the
            # detected MIFARE Classic 1K card; other sizes are selected from the
            # detected name below.  The suffix keeps RRG's generated files tied
            # to this named Workshop backup.
            nm=(self.last_tag.get('name') or '').lower()
            sizeflag='--4k' if '4k' in nm else ('--mini' if 'mini' in nm else ('--2k' if '2k' in nm else '--1k'))
            cmd=f'hf mf autopwn {sizeflag} --suffix {self._quote_pm3(stem)}'
        else:
            cmd=routes[kind].format(f=self._quote_pm3(base))
        self.pending_backup={'base':base,'stem':stem,'tag':dict(self.last_tag),'display_name':friendly,'started':datetime.datetime.now().isoformat(timespec='seconds'),'started_ts':time.time(),'before':before,'cwd':self.backup_dir}
        self.run(cmd)
    def _backup_outputs(self,b,out=''):
        before=set(b.get('before',()))
        started_ts=float(b.get('started_ts',0) or 0)
        candidates=[]
        # Look in the selected library AND the small set of user-local RRG
        # save locations. This handles RRG `savepaths`, which can override cwd.
        for root in self._backup_search_roots():
            try:
                for dp,dn,fn in os.walk(root):
                    dn[:]=[d for d in dn if d not in ('.git','build','obj','armsrc','client','tools','fpga')]
                    for n in fn:
                        if not n.lower().endswith(('.bin','.json','.eml','.mfd','.dump')): continue
                        p=os.path.join(dp,n)
                        try:
                            if p not in before and os.path.getmtime(p)>=started_ts-3 and os.path.getsize(p)>0: candidates.append(p)
                        except OSError: pass
            except OSError: pass
        # Consume the filenames printed by RRG.  Modern RRG save helpers emit
        # lines such as:
        #   Saved 1024 bytes to binary file `/configured/dump/path/name.bin`
        #   Saved to json file /configured/dump/path/name.json
        #   Found keys have been dumped to `/configured/dump/path/key.bin`
        # The old generic regex missed the first form because text appears
        # between 'Saved' and 'to'.  That made a successful autopwn look like
        # a failed Workshop backup whenever RRG's configured dump savepath was
        # outside the few directories Workshop happened to scan.
        clean=ANSI_RE.sub('',out or '')
        printed=[]
        patterns=(
            r'(?im)Saved(?:\s+\d+\s+bytes)?\s+to(?:\s+(?:binary|json))?\s+file\s+[`"\']([^`"\'\r\n]+)[`"\']',
            r'(?im)Saved\s+to\s+json\s+file\s+[`"\']?([^`"\'\r\n]+)',
            r'(?im)(?:dumped|written|saved)\s+to\s+[`"\']([^`"\'\r\n]+)[`"\']',
        )
        for pat in patterns:
            printed.extend(re.findall(pat,clean))
        for raw in printed:
            raw=raw.strip().rstrip(' .')
            # RRG normally prints the resolved savepath.  If an older client
            # prints a relative name, resolve it against the PM3 working dir.
            p=os.path.expanduser(raw)
            if not os.path.isabs(p): p=os.path.join(b.get('cwd') or self.backup_dir,p)
            p=os.path.abspath(p)
            if os.path.isfile(p) and p not in candidates:
                try:
                    if os.path.getmtime(p)>=started_ts-5 and os.path.getsize(p)>0:candidates.append(p)
                except OSError:pass
        # Bring every newly-created RRG companion file into the chosen Workshop
        # library.  Never delete the RRG source copy; this is a safe capture.
        captured=[]; used=set()
        for src in sorted(set(candidates)):
            ext=os.path.splitext(src)[1].lower()
            bn=os.path.basename(src)
            # Preserve RRG's useful key/dump distinction but anchor the friendly name.
            role='keys' if 'key' in bn.lower() else ('dump' if 'dump' in bn.lower() else 'data')
            dest=os.path.join(self.backup_dir,f"{b['stem']}_{role}{ext}")
            i=2; root,ex=os.path.splitext(dest)
            while dest in used or (os.path.exists(dest) and os.path.abspath(dest)!=os.path.abspath(src)):
                dest=f'{root}_{i}{ex}'; i+=1
            used.add(dest)
            try:
                if os.path.abspath(src)!=os.path.abspath(dest): shutil.copy2(src,dest)
                captured.append(dest)
            except OSError: pass
        return captured
    def _finish_backup(self,cmd,out):
        b=getattr(self,'pending_backup',None)
        if not b:return None
        self.pending_backup=None; files=self._backup_outputs(b,out)
        # For Classic, a key file alone is not a backup: require a card-memory
        # dump.  For other families prefer BIN/MFD/DUMP, then EML/JSON.
        kind=b['tag'].get('kind')
        def is_memory(p):
            n=os.path.basename(p).lower(); ext=os.path.splitext(p)[1].lower()
            if kind=='mfc' and 'key' in n:return False
            return ext in ('.bin','.mfd','.dump','.eml','.json')
        priority={'.bin':0,'.mfd':1,'.dump':2,'.eml':3,'.json':8}
        native=sorted([p for p in files if is_memory(p)],key=lambda p:(priority.get(os.path.splitext(p)[1].lower(),5),p))
        primary=native[0] if native else None
        usable=bool(primary)
        meta={'created':b['started'],'display_name':b.get('display_name',os.path.basename(b['base'])),'technology':b['tag'].get('name'),'kind':kind,'uid':b['tag'].get('uid',''),'dump_file':os.path.basename(primary) if primary else '','files':[os.path.basename(x) for x in files],'bytes':os.path.getsize(primary) if primary else 0,'complete':usable,'pm3_command':cmd}
        if usable:
            metapath=primary+'.workshop.json'
            try:
                with open(metapath,'w',encoding='utf-8') as f:json.dump(meta,f,indent=2)
            except Exception:pass
            self._record_history('Backup saved',b['tag'],True); self.after(0,self.refresh_saved_library)
            filelist='\n'.join('• '+os.path.basename(x) for x in files)
            self.after(0,lambda p=primary,fl=filelist:messagebox.showinfo('Backup Saved',f'✓ Backup saved successfully.\n\n{p}\n\nRRG files captured:\n{fl}'))
            return True
        self._record_history('Backup failed',b['tag'],False)
        detail='For MIFARE Classic, Workshop now uses RRG autopwn so the required sector keys are recovered/checked before the card dump is saved. Check Technical Details for the exact RRG result.' if kind=='mfc' else 'Check Technical Details for the exact RRG result.'
        self.after(0,lambda d=detail:messagebox.showwarning('Backup Not Saved','The PM3 operation finished but no complete card-memory dump was produced.\n\n'+d))
        return False
    def restore_backup(self):
        if not self.last_tag:
            messagebox.showwarning('Restore Backup','Scan the target tag first.'); return
        if self.last_tag.get('kind')!='mfc':
            messagebox.showinfo('Restore Backup','Guided restore is currently limited to MIFARE Classic backups so the target can be checked first.'); return
        path=filedialog.askopenfilename(title='Select MIFARE Classic Backup',initialdir=self.backup_dir,filetypes=[('Proxmark binary dump','*.bin'),('All files','*.*')])
        if not path:return
        meta={}
        try:
            with open(path+'.json','r',encoding='utf-8') as f:meta=json.load(f)
        except Exception:pass
        if meta and meta.get('kind')!='mfc':
            messagebox.showerror('Restore Backup','This backup metadata does not describe a MIFARE Classic card.'); return
        if not messagebox.askyesno('Confirm Restore',f'Target: {self.last_tag.get("name")}\nUID: {self.last_tag.get("uid") or "n/a"}\n\nBackup: {os.path.basename(path)}\n\nThis writes card memory. Continue only if you own or are authorised to restore this tag.'):
            return
        self.run('hf mf restore -f '+self._quote_pm3(path))

    def parse_tag(self,out):
        clean=ANSI_RE.sub('',out); low=clean.lower(); uid=''
        m=re.search(r'\bUID:\s*([^\r\n]+)',clean,re.I)
        if m: uid=m.group(1).strip()

        # Never infer a technology from words that merely occur in search/help/error text.
        # RRG `auto` may finish by saving an LF trace when both LF and HF searches fail;
        # that is a signal capture, not a decoded ISO15693 (or other) tag.
        search_failed = ('failed both lf / hf search' in low or
                         ("couldn't identify a chipset" in low and 'valid iso 14443-a tag found' not in low))
        trace_match=re.search(r"saved\s+\d+\s+bytes\s+to\s+pm3 file\s+[`']?([^`'\r\n]+\.pm3)", clean, re.I)
        if search_failed:
            if trace_match:
                return {'kind':'unknown_lf','name':'Unknown LF signal','uid':'',
                        'extra':'Signal captured for analysis', 'trace':trace_match.group(1).strip()}
            return None

        static='static nonce' in low and 'yes' in low
        # Families below require positive identification phrases, not a bare family name.
        positive=[
            # Current RRG specialist HF families.  These are matched only after
            # probe/search chatter has been removed from evidence_low below.
            (('german identification card','german identification card'), 'epa','German eID / EPA'),
            (('pace nonce','pace nonce'), 'epa','German eID / EPA'),
            (('fmcos','fmcos'), 'fmcos','FMCOS CPU Card'),
            (('ict credential','ict credential'), 'ict','ICT Credential'),
            (('jooki','jooki'), 'jooki','Jooki Token'),
            (('apple vas','apple vas'), 'vas','Apple VAS'),
            (('value added service','value added service'), 'vas','Apple VAS'),
            (('waveshare','waveshare'), 'waveshare','Waveshare NFC ePaper'),
            (('gallagher desfire','gallagher desfire'), 'hfgallagher','Gallagher HF Credential'),
            (('em4x50','em4x50'), 'em4x50','EM4x50 / EM4350 / EM4450'),
            (('em 4x50','em 4x50'), 'em4x50','EM4x50 / EM4350 / EM4450'),
            (('em4x70','em4x70'), 'em4x70','EM4x70 / EM4070 / EM4170'),
            (('em 4x70','em 4x70'), 'em4x70','EM4x70 / EM4070 / EM4170'),
            (('hitag u','hitag u'), 'hitaghtu','Hitag µ / 8265'),
            (('hitag µ','hitag µ'), 'hitaghtu','Hitag µ / 8265'),
            (('visa2000','visa2000'), 'visa2000','Visa2000'),
            (('topaz','topaz'), 'topaz','Topaz / NFC Type 1'),
            (('cipurse','cipurse'), 'cipurse','CIPURSE'),
            (('calypso','calypso'), 'calypso','Calypso'),
            (('aliro','aliro'), 'aliro','Aliro'),
            (('emrtd','emrtd'), 'emrtd','eMRTD / Travel Document'),
            (('fido2','fido2'), 'fido','FIDO / FIDO2'),
            (('fido','fido'), 'fido','FIDO / FIDO2'),
            (('smart tap','smart tap'), 'gst','Google Smart Tap'),
            (('google smart tap','google smart tap'), 'gst','Google Smart Tap'),
            (('ksx6924','ksx6924'), 'ksx6924','KS X 6924 / T-Money'),
            (('t-money','t-money'), 'ksx6924','KS X 6924 / T-Money'),
            (('saflok','saflok'), 'saflok','Saflok'),
            (('se config card','se config card'), 'secc','iCLASS SE Config Card'),
            (('tesla','tesla'), 'tesla','Tesla NFC Card'),
            (('texkom','texkom'), 'texkom','Texkom'),
            (('thinfilm','thinfilm'), 'thinfilm','Thinfilm / NFC Barcode'),
            (('nfc barcode','nfc barcode'), 'thinfilm','Thinfilm / NFC Barcode'),
            (('fuji/xerox','fuji/xerox'), 'xerox','Fuji/Xerox Cartridge RFID'),
            (('xerox','xerox'), 'xerox','Fuji/Xerox Cartridge RFID'),
            (('fudan','fudan'), 'fudan','Fudan RFID'),
            (('mifare classic 1k','mifare classic 1k'), 'mfc','MIFARE Classic 1K'),
            (('mifare classic 4k','mifare classic 4k'), 'mfc','MIFARE Classic 4K'),
            (('ntag 424','ntag 424'), 'ntag424','NTAG 424 DNA'),
            (('ntag424','ntag424'), 'ntag424','NTAG 424 DNA'),
            (('mifare ultralight','mifare ultralight'), 'mfu','MIFARE Ultralight / NTAG'),
            (('ntag','ntag'), 'mfu','NTAG'),
            (('felica','felica'), 'felica','FeliCa / ISO18092'),
            (('iso14443-b','iso14443-b'), 'iso14443b','ISO14443-B'),
            (('iso 14443-b','iso 14443-b'), 'iso14443b','ISO14443-B'),
            (('valid iso14443-b','valid iso14443-b'), 'iso14443b','ISO14443-B'),
            (('valid iso 14443-b','valid iso 14443-b'), 'iso14443b','ISO14443-B'),
            (('seos','seos'), 'seos','HID Seos'),
            (('st25ta','st25ta'), 'st25ta','ST25TA NFC Type 4'),
            (('mifare plus','mifare plus'), 'mfp','MIFARE Plus'),
            (('desfire','desfire'), 'desfire','MIFARE DESFire'),
            (('hitag','hitag'), 'hitag','Hitag'),
            (('em410','em410'), 'em410','EM410x'),
            (('em 410','em 410'), 'em410','EM410x'),
            (('hid prox','hid prox'), 'hidprox','HID Prox'),
            (('awid','awid'), 'awid','AWID'),
            (('indala','indala'), 'indala','Indala'),
            (('fdx-b','fdx-b'), 'fdxb','FDX-B Animal ID'),
            (('fdxb','fdxb'), 'fdxb','FDX-B Animal ID'),
            (('nedap','nedap'), 'nedap','NEDAP'),
            (('paradox','paradox'), 'paradox','Paradox'),
            (('presco','presco'), 'presco','Presco'),
            (('securakey','securakey'), 'securakey','Securakey'),
            (('jablotron','jablotron'), 'jablotron','Jablotron'),
            (('keri','keri'), 'keri','KERI'),
            (('gproxii','gproxii'), 'gproxii','Guardall G-Prox II'),
            (('em4x05','em4x05'), 'em4x05','EM4x05 / EM4x69'),
            (('em 4x05','em 4x05'), 'em4x05','EM4x05 / EM4x69'),
            (('cotag','cotag'), 'cotag','COTAG'),
            (('ioprox','ioprox'), 'ioprox','Kantech ioProx'),
            (('io prox','io prox'), 'ioprox','Kantech ioProx'),
            (('pyramid','pyramid'), 'pyramid','Farpointe / Pyramid'),
            (('farpointe','farpointe'), 'pyramid','Farpointe / Pyramid'),
            (('viking','viking'), 'viking','Viking'),
            (('trovan','trovan'), 'trovan','Trovan'),
            (('pcf7931','pcf7931'), 'pcf7931','PCF7931'),
            (('pac/stanley','pac/stanley'), 'pac','PAC / Stanley'),
            (('pac tag','pac tag'), 'pac','PAC / Stanley'),
            (('destron','destron'), 'destron','FDX-A Destron'),
            (('texas instruments','texas instruments'), 'ti','TI LF transponder'),
            (('ti 134','ti 134'), 'ti','TI 134 kHz transponder'),
            (('gallagher','gallagher'), 'gallagher','Gallagher'),
            (('idteck','idteck'), 'idteck','IDTECK'),
            (('motorola','motorola'), 'motorola','Motorola FlexPass'),
            (('flexpass','flexpass'), 'motorola','Motorola FlexPass'),
            (('nexwatch','nexwatch'), 'nexwatch','NexWatch'),
            (('noralsy','noralsy'), 'noralsy','Noralsy'),
            (('lto-cm','lto-cm'), 'lto','LTO Cartridge Memory'),
            (('lto cartridge','lto cartridge'), 'lto','LTO Cartridge Memory'),
            (('valid legic','valid legic'), 'legic','LEGIC Prime'),
            (('legic prime','legic prime'), 'legic','LEGIC Prime'),
            (('chipset... t55','chipset... t55'), 't55xx','T55xx family'),
            (('chipset: t55','chipset: t55'), 't55xx','T55xx family'),
            (('t55','t55'), 't55xx','T55xx family')]
        evidence_low='\n'.join(line.lower() for line in clean.splitlines() if not re.search(r'(?i)\b(searching for|checking for|trying|probe|probing)\b', line))
        for (needle,_),kind,name in positive:
            if needle in evidence_low:
                return {'kind':kind,'name':name,'uid':uid,'extra':'Static nonce: Yes' if kind=='mfc' and static else ''}

        # iCLASS/Picopass must have a positive card response.  `hf search` prints
        # protocol names/hints while probing, so a bare word "iCLASS" or "Picopass"
        # is NOT evidence that the card is iCLASS.
        iclass_positive = any(x in low for x in (
            'valid iclass tag found', 'valid picopass tag found',
            'iclass tag found', 'picopass tag found',
            'picopass csn', 'iclass csn'))
        # A successful iCLASS reader/info response normally exposes a CSN together
        # with iCLASS/Picopass-specific fields.  Do not accept generic UID/search text.
        if not iclass_positive and re.search(r'(?im)^\s*(?:\[.\]\s*)?csn\s*[:|]', clean):
            iclass_positive = any(x in low for x in ('epurse', 'pagemode', 'app limit', 'picopass'))
        if iclass_positive and not any(x in low for x in ('no iclass tag found','no picopass tag found')):
            csn=re.search(r'(?im)^\s*(?:\[.\]\s*)?csn\s*[:|]\s*([^\r\n|]+)', clean)
            return {'kind':'iclass','name':'iCLASS / Picopass','uid':(csn.group(1).strip() if csn else uid),'extra':''}

        # ISO15693 needs evidence of a successful vicinity-tag response.  A bare
        # "ISO15693" string can appear in generic search output and must not classify a tag.
        iso15_positive = any(x in low for x in (
            'valid iso15693 tag found', 'valid iso 15693 tag found',
            'iso15693 tag found', 'iso 15693 tag found',
            'iso15693 uid', 'iso 15693 uid', 'inventory response'))
        if iso15_positive and not any(x in low for x in ('no tag found','no card found','timeout')):
            return {'kind':'iso15693','name':'ISO15693','uid':uid,'extra':''}

        # EMV output is positive only when an application was actually selected/read.
        # Never store PAN/Track 2 in the tag model used by the normal UI.
        if ('debit mastercard' in low or 'credit mastercard' in low or
            re.search(r'\b(?:visa|mastercard|maestro|amex|american express)\b', low)) and \
           ('currency code' in low or 'expiration date' in low or 'application' in low):
            # Do not treat the EMV application label/AID description as the card issuer.
            # Generic Visa/Mastercard applications are shared by many issuers, and some
            # PM3 resource labels are product descriptions rather than authoritative issuer data.
            network='EMV Payment Card'
            if re.search(r'\b(?:mastercard|master card|maestro)\b', low):
                network='Mastercard / EMV Payment Card'
            elif re.search(r'\bvisa\b', low):
                network='Visa / EMV Payment Card'
            elif re.search(r'\b(?:amex|american express)\b', low):
                network='American Express / EMV Payment Card'

            # Only promote an issuer when the card/output explicitly identifies an issuer.
            # Never infer Amazon/Klarna/a bank merely from a generic AID or application label.
            issuer=''
            im=re.search(r'(?im)^\s*Issuer(?: Name)?\.{0,20}[: ]+([^\r\n]+)', clean)
            if im:
                candidate=im.group(1).strip(' .')
                if candidate and candidate.lower() not in ('unknown','n/a','none'):
                    issuer=candidate
            name=(issuer+' • '+network) if issuer else network
            return {'kind':'emv','name':name,'uid':uid,
                    'extra':'Issuer shown only when explicitly reported • Payment data masked'}

        if ('iso14443-4' in low or 'iso 14443-4' in low or 'rats supported' in low) and 'valid iso 14443-a tag found' in low:
            return {'kind':'type4a','name':'NFC Type 4A / ISO14443-4 tag','uid':uid,'extra':''}
        if 'valid iso 14443-a tag found' in low:
            return {'kind':'hf14a','name':'ISO14443-A tag','uid':uid,'extra':''}
        return None
    def refine_tag_identity(self, tag, out):
        """Keep the most specific chip/tag identity that RRG actually reports.
        Never invent a silicon part from a protocol-only response."""
        if not tag: return tag
        clean=ANSI_RE.sub('',out or ''); low=clean.lower()
        candidates=[
            ('ntag 424 dna','NTAG 424 DNA'),('ntag424','NTAG 424 DNA'),
            ('st25ta','ST25TA NFC Type 4'),
            ('ntag 216','NTAG216'),('ntag216','NTAG216'),('ntag 215','NTAG215'),('ntag215','NTAG215'),('ntag 213','NTAG213'),('ntag213','NTAG213'),
            ('ultralight ev1','MIFARE Ultralight EV1'),('ultralight c','MIFARE Ultralight C'),
            ('desfire ev3','MIFARE DESFire EV3'),('desfire ev2','MIFARE DESFire EV2'),('desfire ev1','MIFARE DESFire EV1'),
            ('t5577','T5577'),('t55x7','T55x7'),('t55xx','T55xx family'),('t5555','T5555 / Q5'),('q5','Q5 / T5555'),
            ('em4305','EM4305'),('em4469','EM4469'),('em4205','EM4205'),('em4102','EM4102'),('em4100','EM4100 / EM4102'),
            ('hitag 2','Hitag 2'),('hitag s','Hitag S'),('hitag 1','Hitag 1'),
            ('icode slix2','ICODE SLIX2'),('icode slix-s','ICODE SLIX-S'),('icode slix','ICODE SLIX'),('icode sli','ICODE SLI'),
            ('st25tv','ST25TV / ISO15693'),('st25dv','ST25DV / ISO15693')
        ]
        for needle,name in candidates:
            if needle in low:
                tag=dict(tag); tag['name']=name; tag['chip_exact']=True; return tag
        tag=dict(tag); tag['chip_exact']=False
        return tag

    def _tag_registry(self):
        """Single source of truth for detected-family UI capabilities.
        Commands are intentionally conservative: read/inspect operations are exposed
        by default; destructive/security-sensitive actions remain separately guarded.
        """
        return {
          'epa': {'frequency':'HF • 13.56 MHz','protocol':'German eID / PACE','info':'hf epa help','actions':[('PACE Module Help','hf epa help')], 'writable':'No normal Workshop route','backup':'No generic route','simulate':'Expert only','ndef':'Not applicable','authentication':'PACE / document credentials'},
          'fmcos': {'frequency':'HF • 13.56 MHz','protocol':'FMCOS CPU / ISO7816 card','info':'hf fmcos info','actions':[('Information','hf fmcos info'),('Select File / App','hf fmcos select'),('Read Binary','hf fmcos readbinary'),('Read Record','hf fmcos readrecord')], 'writable':'Access-right dependent','backup':'File/application based','simulate':'No generic route','ndef':'Application dependent','authentication':'DES/3DES / application dependent'},
          'ict': {'frequency':'HF • 13.56 MHz','protocol':'ICT MIFARE / DESFire credential','info':'hf ict info','actions':[('Information','hf ict info'),('Read Credential','hf ict credential'),('Reader','hf ict reader')], 'writable':'Underlying card dependent','backup':'Use underlying MIFARE/DESFire tools','simulate':'Underlying card dependent','ndef':'Not normally applicable','authentication':'Credential/card dependent'},
          'jooki': {'frequency':'HF • 13.56 MHz','protocol':'Jooki token','info':'hf jooki decode','actions':[('Decode Token','hf jooki decode')], 'writable':'Yes / token workflow','backup':'Decoded token data','simulate':'Yes','ndef':'Not applicable','authentication':'Token format dependent'},
          'vas': {'frequency':'HF • 13.56 MHz','protocol':'Apple Value Added Service','info':'hf vas info','actions':[('Information','hf vas info'),('Reader','hf vas reader')], 'writable':'No generic tag-write route','backup':'Captured message only','simulate':'No generic route','ndef':'Not applicable','authentication':'VAS cryptography'},
          'waveshare': {'frequency':'HF • 13.56 MHz','protocol':'Waveshare NFC ePaper','info':'hf waveshare help','actions':[('Module Help','hf waveshare help')], 'writable':'Image-load workflow','backup':'No generic tag dump','simulate':'No','ndef':'Not applicable','authentication':'Model dependent'},
          'hfgallagher': {'frequency':'HF • 13.56 MHz','protocol':'Gallagher MIFARE / DESFire credential','info':'hf gallagher reader','actions':[('Read / Decode Credentials','hf gallagher reader')], 'writable':'Underlying card/key dependent','backup':'Use underlying MIFARE/DESFire backup','simulate':'Underlying card dependent','ndef':'Not applicable','authentication':'Gallagher diversified keys'},
          'em4x50': {'frequency':'LF • 125 kHz','protocol':'EM4350 / EM4450','info':'lf em 4x50 info','actions':[('Information','lf em 4x50 info'),('Reader','lf em 4x50 reader'),('Dump / Backup','lf em 4x50 dump'),('Read Word','lf em 4x50 rdbl')], 'writable':'Yes / password dependent','backup':'Yes','simulate':'Yes','ndef':'Not applicable','authentication':'Optional password'},
          'em4x70': {'frequency':'LF • 125 kHz','protocol':'EM4070 / EM4170','info':'lf em 4x70 info','actions':[('Information','lf em 4x70 info')], 'writable':'Yes / authentication dependent','backup':'No generic dump command','simulate':'No generic route','ndef':'Not applicable','authentication':'Key/PIN authentication'},
          'hitaghtu': {'frequency':'LF • 125 kHz','protocol':'Hitag µ / 8265','info':'lf hitag htu reader','actions':[('Reader','lf hitag htu reader'),('Dump / Backup','lf hitag htu dump'),('Read Block','lf hitag htu rdbl')], 'writable':'Yes / tag dependent','backup':'Yes','simulate':'Yes','ndef':'Not applicable','authentication':'Variant dependent'},
          'visa2000': {'frequency':'LF • 125 kHz','protocol':'Visa2000','info':'lf visa2000 reader','actions':[('Read / Identify','lf visa2000 reader')], 'writable':'Clone-target dependent','backup':'Credential data','simulate':'Yes','ndef':'Not applicable','authentication':'None / protocol dependent'},
          'topaz': {'frequency':'HF • 13.56 MHz','protocol':'NFC Forum Type 1 / Topaz','info':'hf topaz info','actions':[('Information','hf topaz info'),('Reader','hf topaz reader'),('Dump / Backup','hf topaz dump'),('Read Block','hf topaz rdbl')], 'writable':'Yes / lock-state dependent','backup':'Yes','simulate':'Yes','ndef':'Reported by Topaz info when present','authentication':'Tag dependent'},
          'cipurse': {'frequency':'HF • 13.56 MHz','protocol':'CIPURSE / ISO14443','info':'hf cipurse info','actions':[('Information','hf cipurse info'),('Select Application / File','hf cipurse select'),('Read File','hf cipurse read'),('Read Attributes','hf cipurse aread')], 'writable':'Access-right dependent','backup':'File/application based','simulate':'No generic route','ndef':'Application dependent','authentication':'CIPURSE keys'},
          'calypso': {'frequency':'HF • 13.56 MHz','protocol':'Calypso transport card','info':'hf calypso info','actions':[('Information','hf calypso info'),('Dump / Profile Scan','hf calypso dump'),('Probe SELECT Compatibility','hf calypso probecmdcompat')], 'writable':'Application dependent','backup':'Profile dump','simulate':'No generic route','ndef':'Not normally applicable','authentication':'Application dependent'},
          'aliro': {'frequency':'HF • 13.56 MHz','protocol':'Aliro digital access credential','info':'hf aliro info','actions':[('Information','hf aliro info'),('Read / Document Retrieval','hf aliro read')], 'writable':'Credential dependent','backup':'Application dependent','simulate':'No generic route','ndef':'Not applicable','authentication':'Cryptographic credential'},
          'emrtd': {'frequency':'HF • 13.56 MHz','protocol':'eMRTD / ICAO travel document','info':'hf emrtd info','actions':[('Information','hf emrtd info'),('Dump Document Files','hf emrtd dump')], 'writable':'No normal Workshop route','backup':'Protected/document dependent','simulate':'No generic route','ndef':'Not applicable','authentication':'Travel-document access controls'},
          'fido': {'frequency':'HF • 13.56 MHz','protocol':'FIDO / FIDO2 authenticator','info':'hf fido info','actions':[('Information','hf fido info')], 'writable':'Protocol operation dependent','backup':'No generic route','simulate':'No generic route','ndef':'Not applicable','authentication':'FIDO/U2F/FIDO2'},
          'gst': {'frequency':'HF • 13.56 MHz','protocol':'Google Smart Tap','info':'hf gst info','actions':[('Information','hf gst info'),('Read Pass Objects','hf gst read')], 'writable':'Pass dependent','backup':'Decoded objects only','simulate':'No generic route','ndef':'Not generic NDEF','authentication':'Smart Tap protocol'},
          'ksx6924': {'frequency':'HF • 13.56 MHz','protocol':'KS X 6924 / transport card','info':'hf ksx6924 info','actions':[('Information','hf ksx6924 info'),('Balance','hf ksx6924 balance')], 'writable':'Transaction/application dependent','backup':'No generic dump route','simulate':'No generic route','ndef':'Not applicable','authentication':'Application dependent'},
          'saflok': {'frequency':'HF • 13.56 MHz','protocol':'Saflok / MIFARE Classic application','info':'hf saflok interrogate','actions':[('Interrogate Card','hf saflok interrogate'),('Read Card','hf saflok read')], 'writable':'Application/key dependent','backup':'Use underlying MIFARE tools when authorised','simulate':'Underlying tag dependent','ndef':'Not applicable','authentication':'Saflok/MIFARE keys'},
          'secc': {'frequency':'HF • 13.56 MHz','protocol':'HID iCLASS SE Config Card','info':'hf secc info','actions':[('Information','hf secc info')], 'writable':'No normal route','backup':'No generic route','simulate':'Yes','ndef':'Not applicable','authentication':'Application dependent'},
          'tesla': {'frequency':'HF • 13.56 MHz','protocol':'Tesla NFC card/application','info':'hf tesla info','actions':[('Information','hf tesla info')], 'writable':'Application dependent','backup':'No generic route','simulate':'No generic route','ndef':'Not applicable','authentication':'Application dependent'},
          'texkom': {'frequency':'HF • 13.56 MHz','protocol':'Texkom RFID','info':'hf texkom reader','actions':[('Reader','hf texkom reader')], 'writable':'Unknown / protocol dependent','backup':'No generic route','simulate':'Yes','ndef':'Not applicable','authentication':'Protocol dependent'},
          'thinfilm': {'frequency':'HF • 13.56 MHz','protocol':'Thinfilm / NFC Barcode','info':'hf thinfilm info','actions':[('Information','hf thinfilm info')], 'writable':'Tag dependent','backup':'No generic dump route','simulate':'Yes','ndef':'NFC Barcode rather than normal NDEF','authentication':'None / tag dependent'},
          'xerox': {'frequency':'HF • 13.56 MHz','protocol':'Fuji/Xerox cartridge RFID','info':'hf xerox info','actions':[('Information','hf xerox info'),('Reader','hf xerox reader'),('Dump / Backup','hf xerox dump'),('Read Block','hf xerox rdbl')], 'writable':'Model dependent','backup':'Yes','simulate':'No generic route','ndef':'Not applicable','authentication':'Model dependent'},
          'fudan': {'frequency':'HF • 13.56 MHz','protocol':'Fudan RFID','info':'hf fudan reader','actions':[('Reader','hf fudan reader'),('Dump / Backup','hf fudan dump'),('Read Block','hf fudan rdbl')], 'writable':'Yes / tag dependent','backup':'Yes','simulate':'No generic route','ndef':'Tag dependent','authentication':'Model dependent'},
          'felica': {'frequency':'HF • 13.56 MHz','protocol':'FeliCa / ISO18092','info':'hf felica info','actions':[('Information','hf felica info'),('Read Block','hf felica rdbl'),('FeliCa Lite Dump','hf felica litedump'),('Discover Services','hf felica discnodes')], 'writable':'Model/service dependent','backup':'FeliCa Lite only','simulate':'FeliCa Lite','ndef':'No generic NDEF route','authentication':'Model/service dependent'},
          'iso14443b': {'frequency':'HF • 13.56 MHz','protocol':'ISO14443-B / NFC Type 4B','info':'hf 14b info','actions':[('Information','hf 14b info'),('Reader','hf 14b reader'),('Read NDEF','hf 14b ndefread'),('Dump / Backup','hf 14b dump')], 'writable':'Tag/model dependent','backup':'Yes','simulate':'Yes','ndef':'Supported when present','authentication':'Application dependent'},
          'ntag424': {'frequency':'HF • 13.56 MHz','protocol':'ISO14443-A / NTAG 424 DNA','info':'hf ntag424 info','actions':[('Information','hf ntag424 info'),('Read File','hf ntag424 read'),('File Settings','hf ntag424 getfs')], 'writable':'Yes / access-right dependent','backup':'File based','simulate':'No generic route','ndef':'Secure Dynamic Messaging / file based','authentication':'AES keys'},
          'seos': {'frequency':'HF • 13.56 MHz','protocol':'HID Seos','info':'hf seos info','actions':[('Information','hf seos info'),('Read ADF','hf seos adf'),('Read GDF','hf seos gdf')], 'writable':'Credential/key dependent','backup':'Application dependent','simulate':'Supported','ndef':'Not applicable','authentication':'Cryptographic keys / SAM may apply'},
          'st25ta': {'frequency':'HF • 13.56 MHz','protocol':'ISO14443-A / NFC Type 4','info':'hf st25ta info','actions':[('Information','hf st25ta info'),('Read NDEF','hf st25ta ndefread')], 'writable':'Yes / protection dependent','backup':'NDEF / emulator tools','simulate':'Yes','ndef':'Supported','authentication':'Password/protection may apply'},
          'legic': {'frequency':'HF • 13.56 MHz','protocol':'LEGIC Prime','info':'hf legic info','actions':[('Information','hf legic info'),('Reader','hf legic reader')], 'writable':'Yes / lock-state dependent','backup':'Yes','simulate':'Yes','ndef':'Not applicable','authentication':'Model dependent'},
          'lto': {'frequency':'HF • 13.56 MHz','protocol':'LTO Cartridge Memory','info':'hf lto info','actions':[('Information','hf lto info'),('Dump / Backup','hf lto dump'),('Reader','hf lto reader')], 'writable':'Yes / media dependent','backup':'Yes','simulate':'Unknown','ndef':'Not applicable','authentication':'Media dependent'},
        }

    def _tag_capabilities(self, tag=None):
        tag=tag or self.last_tag or {}; k=tag.get('kind','')
        c={'writable':'Unknown','erase':'No','backup':'No','simulate':'No','ndef':'No','authentication':'Unknown'}
        reg=self._tag_registry().get(k)
        if reg:
            c.update(writable=reg.get('writable','Unknown'),backup=reg.get('backup','No'),simulate=reg.get('simulate','No'),ndef=reg.get('ndef','No'),authentication=reg.get('authentication','Unknown'))
        if k in ('em410','hidprox','awid','indala','fdxb','nedap','paradox','presco','securakey','jablotron','keri','gproxii','cotag','ioprox','pyramid','viking','trovan','pac','destron','gallagher','idteck','motorola','nexwatch','noralsy'):
            c.update(writable='No / credential dependent',simulate='Yes' if k not in ('cotag','trovan') else 'Protocol dependent',authentication='None / protocol dependent')
        if k=='t55xx': c.update(writable='Yes',erase='Yes',backup='Yes',simulate='Via encoded protocol',authentication='Optional password')
        if k=='em4x05': c.update(writable='Yes',erase='Yes',backup='Yes',authentication='Optional password')
        if k=='em4x50': c.update(writable='Yes / password dependent',erase='Yes',backup='Yes',simulate='Yes',authentication='Optional password')
        if k=='hitaghtu': c.update(writable='Yes / tag dependent',backup='Yes',simulate='Yes',authentication='Variant dependent')
        if k in ('mfc','mfu','iso15693','legic'): c.update(writable='Yes / lock-state dependent',erase='Yes',backup='Yes',simulate='Yes')
        if k in ('mfc','mfu','mfp','type4a'): c['ndef']='Supported when configured'
        if k in ('iclass','lto'): c['backup']='Yes'
        if k=='lto': c['writable']='Yes / media dependent'
        if k in ('pcf7931','ti'): c['writable']='Model/config dependent'
        return c

    def _rrg_workflow_policy(self, kind):
        """Audited against current RRG command families (2026-09).

        `verified_restore` means Workshop has a native RRG restore AND a native
        read-back route that can be compared.  A command returning success is
        never itself considered verification.  Other families stay read/backup
        only or Expert/specialised until their authentication/model requirements
        are known at runtime.
        """
        verified_restore={'mfc','mfu','iso15693','iso14443b','legic','lto','t55xx'}
        native_backup={'mfc','mfu','mfp','desfire','iso15693','iso14443b','iclass','legic','lto','topaz','xerox','fudan','em4x05','em4x50','t55xx','hitag','hitaghtu'}
        protocol_clone={'em410','hidprox','awid','indala','fdxb','nedap','paradox','presco','securakey','jablotron','keri','gproxii','ioprox','pyramid','viking','trovan','pac','destron','idteck','motorola','nexwatch','noralsy','visa2000'}
        special={'mfp','desfire','ntag424','seos','iclass','felica','em4x70','pcf7931','ti','hitag','hitaghtu','fmcos','cipurse','calypso','aliro','emrtd','fido','gst','ksx6924','saflok','epa','ict','jooki','vas','waveshare','hfgallagher','topaz','texkom','thinfilm','xerox','fudan','st25ta'}
        return {'verified_restore':kind in verified_restore,'native_backup':kind in native_backup,'protocol_clone':kind in protocol_clone,'special':kind in special}

    def _erase_capability(self, tag=None):
        """Return a guarded erase/wipe capability only for positively identified writable families."""
        tag=tag or self.last_tag or {}; kind=tag.get('kind','')
        return {
            'em4x05': {'label':'BLANK / ERASE TAG','cmd':'lf em 4x05 wipe','verify':'lf em 4x05 info','what':'EM4x05 / EM4x69 tag'},
            't55xx': {'label':'BLANK / ERASE TAG','cmd':'lf t55xx wipe','verify':'lf t55xx detect','what':'T55xx tag'},
            'mfc': {'label':'BLANK / ERASE CARD','cmd':'hf mf wipe','verify':'hf mf info','what':'MIFARE Classic card'},
            'mfu': {'label':'BLANK / ERASE TAG','cmd':'hf mfu wipe','verify':'hf mfu info','what':'Ultralight / NTAG tag'},
            'iso15693': {'label':'BLANK / ERASE TAG','cmd':'hf 15 wipe','verify':'hf 15 info','what':'ISO15693 tag'},
            'legic': {'label':'BLANK / ERASE TAG','cmd':'hf legic wipe','verify':'hf legic info','what':'LEGIC Prime tag'},
        }.get(kind)

    def erase_current_tag(self, parent=None):
        """Destructive erase with explicit confirmation and a post-operation read-back check."""
        cap=self._erase_capability()
        if not cap:
            messagebox.showinfo('Erase / Blank','Workshop has not identified a supported erase/reset operation for this card/tag.',parent=parent); return
        if not self.connected:
            messagebox.showwarning(APP,'Connect to the Proxmark3 first.',parent=parent); return
        name=(self.last_tag or {}).get('name',cap['what'])
        msg=(f"ERASE / BLANK {name}\n\nThis is destructive and will remove programmed user data using:\n{cap['cmd']}\n\n"
             "Only continue for a card/tag you intend to erase. This cannot be undone.\n\nContinue?")
        if not messagebox.askyesno('Confirm destructive erase',msg,parent=parent): return
        if self.busy:
            messagebox.showinfo(APP,'A Proxmark3 command is already running.',parent=parent); return
        rw=tk.Toplevel(parent or self); rw.title('Erase / Blank — '+name); rw.configure(bg=self.BG); rw.geometry('760x500');
        if parent:
            try: rw.transient(parent)
            except Exception: pass
        f=ttk.Frame(rw,style='Card.TFrame',padding=14); f.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(f,text='ERASE / BLANK CARD / TAG',style='CardTitle.TLabel').pack(anchor='w')
        status=tk.StringVar(value='Erasing… do not remove the tag.'); ttk.Label(f,textvariable=status,style='Muted.TLabel').pack(anchor='w',pady=(4,8))
        out=tk.Text(f,bg=self.CONSOLE,fg='#c9def6',font=('Monospace',9),wrap='word',padx=10,pady=10); out.pack(fill='both',expand=True)
        def worker():
            try:
                erase_out=self.pm.execute(cap['cmd']) or ''
                low=erase_out.lower()
                hard_fail=any(x in low for x in ('command not found','unknown command','failed to write','write failed','could not write','error:'))
                verify_out=''
                if not hard_fail:
                    verify_out=self.pm.execute(cap['verify']) or ''
                combined=erase_out+'\n\n--- POST-ERASE READ-BACK ---\n'+verify_out
                verified=(not hard_fail and bool(verify_out.strip()) and not any(x in verify_out.lower() for x in ('no tag found','no card found','timeout waiting')))
                err=None
            except Exception as e:
                combined=''; verified=False; err=str(e)
            def finish():
                if not rw.winfo_exists(): return
                out.insert('1.0',combined if combined else ('Error: '+err if err else 'No output returned.')); out.config(state='disabled')
                if err:
                    status.set('✗ ERASE FAILED — '+err)
                elif verified:
                    status.set('✓ ERASE COMMAND COMPLETED • TAG RE-DETECTED AFTER ERASE')
                    messagebox.showinfo('Erase / Blank','Erase completed and the tag responded to the post-erase read-back check.\n\nWorkshop will re-scan it so the overview reflects its new state.',parent=rw)
                    try:
                        scan='lf search' if (self.last_tag or {}).get('kind')=='t55xx' else ('hf search' if (self.last_tag or {}).get('kind') in ('mfc','mfu','iso15693','legic') else cap['verify'])
                        self.after(100,lambda:self.run(scan))
                    except Exception: pass
                else:
                    status.set('⚠ ERASE COULD NOT BE VERIFIED — review the output below')
            self.after(0,finish)
        threading.Thread(target=worker,daemon=True).start()

    def scanner_home(self):
        self._clear_right_page(keep_home=True)
        self._select_page('home')
        self._activity_feedback('Home','Ready to scan LF or HF.',False,None)
        try:
            if not self.home_body.winfo_ismapped(): self.home_body.pack(fill='both',expand=True)
        except Exception: pass
        self.last_tag=None; self.tag_title.config(text='Ready to scan'); self.tag_meta.config(text='Place the card/tag on the correct Proxmark3 antenna, then choose SCAN LF or SCAN HF.'); self.tag_badge.config(text='● Ready',foreground=self.MUTED); self.card_hint.config(text='No card selected. Workshop will automatically choose the correct tools after a scan.'); self._home_actions()
    def _canonical_tag_kind(self, tag):
        """Normalise display/parser variants to one internal technology id.
        Display names must never control compatibility or action routing.
        """
        if not tag:
            return tag
        t=dict(tag)
        raw=(str(t.get('kind',''))+' '+str(t.get('name',''))+' '+str(t.get('family',''))).lower()
        if any(x in raw for x in ('t5577','t55x7','t55xx','t5555',' q5')):
            t['kind']='t55xx'
        return t

    def _extract_t55xx_encoded_credential(self, text):
        """Return (format, id) only from positive decoder output."""
        clean=ANSI_RE.sub('',text or '')
        # Prefer the explicit section Workshop appends after physical T55xx detection.
        part=clean.split('--- ENCODED LF CREDENTIAL ---')[-1] if '--- ENCODED LF CREDENTIAL ---' in clean else ''
        if not part: return ('','')
        low=part.lower()
        if any(x in low for x in ('no tag found','no em410','timeout waiting')): return ('','')
        patterns=(
            r'(?im)^\s*(?:em\s*410x?\s*)?(?:tag\s*)?id\s*[:|]\s*(?:0x)?([0-9a-f]{10})\b',
            r'(?im)^.*?em410x.*?(?:0x)?([0-9a-f]{10})\b',
            r'(?im)^.*?em\s*410.*?(?:0x)?([0-9a-f]{10})\b',
        )
        for pat in patterns:
            m=re.search(pat,part)
            if m:return ('EM410x',m.group(1).upper())
        return ('','')

    def show_tag_menu(self,tag):
        tag=self._canonical_tag_kind(tag)
        self.last_tag=tag
        self._home_actions()
        try:
            self.home_lf_btn.config(style='Primary.TButton' if self.current_cmd=='lf search' else 'HomeAction.TButton')
            self.home_hf_btn.config(style='Primary.TButton' if self.current_cmd=='hf search' else 'HomeAction.TButton')
        except Exception: pass
        if not tag:
            band='LF' if self.current_cmd=='lf search' else ('HF' if self.current_cmd=='hf search' else '')
            self.tag_title.config(text=(f'No {band} tag detected' if band else 'Tag not identified'))
            self.tag_meta.config(text=(f'The {band} scan completed but no supported {band} card/tag was identified. Check Technical Details if a tag is present.' if band else 'The scan completed, but no supported tag type was identified. Check the console for details.'))
            return
        if tag.get('kind')=='unknown_lf':
            self.tag_title.config(text='Unknown LF signal')
            self.tag_badge.config(text='● Signal captured',foreground='#ffbf47')
            trace=tag.get('trace','')
            meta='LF • 125 / 134 kHz\nCard/transponder not identified\nSignal capture available'
            if trace: meta+='\n'+os.path.basename(trace)
            self.tag_meta.config(text=meta)
            self.clear_console()
            self.console.insert('end','UNKNOWN LF SIGNAL\n'+'─'*44+'\n\n')
            self.console.insert('end','The Proxmark3 detected activity but could not identify a supported LF transponder.\n')
            if trace:self.console.insert('end','\n✓ Signal trace already saved:\n'+trace+'\n')
            self.console.insert('end','\nThis is a signal capture, not a decoded card backup.\n')
            if trace:
                ttk.Button(self.dynamic_actions,text='SHOW CAPTURE LOCATION',style='Action.TButton',command=lambda t=trace:messagebox.showinfo('Saved Signal Capture',t)).pack(fill='x',pady=4)
            ttk.Button(self.dynamic_actions,text='TECHNICAL DETAILS',style='Action.TButton',command=lambda:self.set_console_mode(True)).pack(fill='x',pady=4)
            return
        self.tag_title.config(text=tag['name'])
        self.tag_badge.config(text='● Card / tag detected',foreground=self.GREEN)
        freq='LF • 125 / 134 kHz' if tag['kind'] in ('hitag','em410','t55xx','em4x05','hidprox','awid','indala','fdxb','nedap','paradox','presco','securakey','jablotron','keri','gproxii','cotag','ioprox','pyramid','viking','trovan','pcf7931','pac','destron','ti','gallagher','idteck','motorola','nexwatch','noralsy') else 'HF • 13.56 MHz'
        rows=[f"Chip / Tag   {tag['name']}",freq]
        if tag.get('uid'): rows.append(f"UID      {tag['uid']}")
        if tag['kind']=='t55xx':
            if tag.get('encoded_format'):
                rows.append(f"Encoded format   {tag['encoded_format']}")
                rows.append(f"Credential ID    {tag.get('credential_id') or 'Decoded, ID unavailable'}")
                rows.append('Status           Programmed / readable')
            else:
                rows.append('Encoded format   None detected')
                rows.append('Credential ID    None decoded')
                rows.append('Status           T55xx chip detected')
        if tag.get('extra'): rows.append(tag['extra'].replace('Static nonce: Yes','Nonce    Static (re-used)'))
        if tag['kind']=='mfc': rows.append('NDEF     Use View / Manage NDEF')
        self.tag_meta.config(text='\n'.join(rows)); self.card_hint.config(text='Card / tag identified. Workshop has loaded the tools that apply to this detected technology.'); self.show_clean_tag_header(tag)
        info_cmd={'mfc':'hf mf info','mfu':'hf mfu info','mfp':'hf mfp info','type4a':'hf 14a info','desfire':'hf mfdes info','iso15693':'hf 15 info','legic':'hf legic info','iclass':'hf iclass reader','hitag':'lf hitag info','em410':'lf em 410x reader','hidprox':'lf hid reader','awid':'lf awid reader','indala':'lf indala reader','fdxb':'lf fdxb reader','nedap':'lf nedap reader','paradox':'lf paradox reader','presco':'lf presco reader','securakey':'lf securakey reader','jablotron':'lf jablotron reader','keri':'lf keri reader','gproxii':'lf gproxii reader','t55xx':'lf t55xx detect','em4x05':'lf em 4x05 info','cotag':'lf cotag reader','ioprox':'lf io reader','pyramid':'lf pyramid reader','viking':'lf viking reader','trovan':'lf trovan reader','pcf7931':'lf pcf7931 reader','pac':'lf pac reader','destron':'lf destron reader','ti':'lf ti reader','gallagher':'lf gallagher reader','idteck':'lf idteck reader','motorola':'lf motorola reader','nexwatch':'lf nexwatch reader','noralsy':'lf noralsy reader','lto':'hf lto info','felica':'hf felica info','iso14443b':'hf 14b info','ntag424':'hf ntag424 info','seos':'hf seos info','st25ta':'hf st25ta info','hf14a':'hf 14a info','emv':'emv reader'}.get(tag['kind'],'auto')
        info_cmd=self._tag_registry().get(tag['kind'],{}).get('info',info_cmd)
        ttk.Button(self.dynamic_actions,text='CARD / TAG INFORMATION',style='Action.TButton',command=lambda c=info_cmd:self.run(c)).pack(fill='x',pady=4)
        if tag['kind'] in ('mfc','mfu','iso15693','iclass','legic','iso14443b','t55xx'):
            ttk.Button(self.dynamic_actions,text='BACKUP TO FILE',style='Action.TButton',command=self.backup_current_tag).pack(fill='x',pady=4)
        if tag['kind'] in ('mfc','mfu','mfp','type4a','iso14443b','st25ta'):
            read={'mfc':'hf mf ndefread','mfu':'hf mfu ndefread','mfp':'hf mfp ndefread','type4a':'hf 14a ndefread','iso14443b':'hf 14b ndefread','st25ta':'hf st25ta ndefread'}[tag['kind']]
            ttk.Button(self.dynamic_actions,text='VIEW / MANAGE NDEF',style='Action.TButton',command=lambda c=read:self.run(c)).pack(fill='x',pady=4)
        if tag['kind']=='t55xx':
            ttk.Button(self.dynamic_actions,text='PROGRAM EM410X TEST / ID',style='Action.TButton',command=self.program_t55xx_em410x).pack(fill='x',pady=4)
            ttk.Button(self.dynamic_actions,text='RESTORE T55XX DUMP',style='Action.TButton',command=self.restore_t55xx_external).pack(fill='x',pady=4)
        cap=self._erase_capability(tag)
        if cap:
            ttk.Button(self.dynamic_actions,text=cap['label'],style='Action.TButton',command=lambda:self.erase_current_tag(self)).pack(fill='x',pady=4)

    def program_t55xx_em410x(self):
        if not self.connected:return
        ident=simpledialog.askstring('Program T5577 / T55xx','Enter a 10-hex-digit EM410x test/credential ID:',initialvalue='1122334455',parent=self)
        if ident is None:return
        ident=re.sub(r'[^0-9A-Fa-f]','',ident).upper()
        if len(ident)!=10:messagebox.showerror('Program T5577','EM410x ID must be exactly 10 hexadecimal digits (5 bytes).');return
        if not messagebox.askyesno('Confirm T5577 Write',f'Program EM410x ID {ident} to the detected writable T55xx tag?\n\nThis changes the tag contents. Continue only with a tag you own or are authorised to program.'):return
        # Use RRG's native encoder/writer rather than a hand-built T55xx image.
        # RRG documents this command specifically for encoding an EM410x ID onto T55x7.
        if self.busy:
            messagebox.showinfo('Program T5577','Workshop is already running a Proxmark3 operation.'); return
        self.busy=True
        rw=self._tool_window('Program T5577 / T55xx Test Tag','760x470')
        box=ttk.Frame(rw,style='Card.TFrame',padding=14); box.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(box,text='PROGRAM EM410X TEST / ID',style='CardTitle.TLabel').pack(anchor='w')
        status=tk.StringVar(value='● Writing EM410x '+ident+'…')
        detail=tk.StringVar(value='Using RRG native T55x7 encoder. Keep the tag on the LF antenna.')
        ttk.Label(box,textvariable=status,style='TagTitle.TLabel').pack(anchor='w',pady=(8,2))
        ttk.Label(box,textvariable=detail,style='Muted.TLabel',wraplength=700).pack(anchor='w',pady=(0,8))
        bar=ttk.Progressbar(box,mode='indeterminate'); bar.pack(fill='x',pady=(0,8)); bar.start(12)
        log=tk.Text(box,bg=self.CONSOLE,fg='#c9def6',font=('Monospace',9),wrap='word',height=12,padx=8,pady=8); log.pack(fill='both',expand=True)
        rw._restore_status=status; rw._restore_detail=detail; rw._restore_bar=bar; rw._restore_log=log
        self._activity_feedback('PROGRAMMING T55XX','Encoding and writing EM410x '+ident+' with RRG native clone command…',True,None)
        threading.Thread(target=self._program_t55xx_em410x_worker,args=(rw,ident),daemon=True).start()

    def _program_t55xx_em410x_worker(self,rw,ident):
        try:
            self.q.put(('restore_write_stage',(rw,'● Writing EM410x '+ident+'…','RRG is encoding and writing the credential to the T55x7.')))
            write_out=self.pm.execute('lf em 410x clone --id '+ident) or ''
            low=write_out.lower()
            if any(x in low for x in ('something went wrong','failed','error','timeout')) or 'done!' not in low:
                raise RuntimeError('RRG did not confirm the EM410x clone completed. See the output below.')
            self.q.put(('restore_write_stage',(rw,'● Reading credential back…','Write completed. Workshop is decoding the tag again before reporting success.')))
            reader=self.pm.execute('lf em 410x reader') or ''
            search=self.pm.execute('lf search') or ''
            verify_out='--- EM410X READER ---\n'+reader+'\n--- LF SEARCH ---\n'+search
            clean=ANSI_RE.sub('',verify_out).upper().replace(' ','')
            verified=ident.upper() in clean
            if verified:
                summary='EM410x '+ident+' decoded successfully after programming.'
            else:
                summary='The write command completed, but Workshop could not decode EM410x '+ident+' on read-back.'
            self.q.put(('t55xx_program_result',(rw,ident,write_out,verify_out,verified,summary,None)))
        except Exception as e:
            self.q.put(('t55xx_program_result',(rw,ident,'','',False,'',str(e))))

    def _finish_t55xx_program(self,payload):
        rw,ident,write_out,verify_out,verified,summary,error=payload
        self.busy=False
        try:
            if not rw.winfo_exists(): return
            rw._restore_bar.stop()
            rw._restore_log.insert('end','--- WRITE OUTPUT ---\n'+(write_out or '(no output)')+'\n\n--- READ-BACK OUTPUT ---\n'+(verify_out or '(no output)')+'\n')
            rw._restore_log.see('end')
            if error:
                rw._restore_status.set('✗ PROGRAM / VERIFY FAILED'); rw._restore_detail.set(error)
                self._activity_feedback('PROGRAM / VERIFY FAILED',error,False,False)
                messagebox.showerror('T5577 Programming Failed',error,parent=rw)
            elif verified:
                rw._restore_status.set('✓ PROGRAM & VERIFY COMPLETE'); rw._restore_detail.set(summary)
                self._activity_feedback('PROGRAM & VERIFY COMPLETE',summary,False,True)
                messagebox.showinfo('T5577 Test Complete',summary+'\n\nThe tag was written using RRG native EM410x encoding and decoded again successfully.',parent=rw)
                # Refresh Home state with the verified credential, without depending on T55xx detect
                # (which can become unreliable once a T55x7 is configured to emulate another LF format).
                self.last_tag={'kind':'t55xx','name':'T55xx family','encoded_format':'EM410x','credential_id':ident}
                self.show_tag_menu(self.last_tag)
            else:
                rw._restore_status.set('✗ WRITE COMPLETED — VERIFY FAILED'); rw._restore_detail.set(summary)
                self._activity_feedback('VERIFY FAILED',summary,False,False)
                messagebox.showwarning('T5577 Verification Failed',summary+'\n\nNothing is being assumed from the write response. Check Technical Details / the output in this window.',parent=rw)
        except Exception:
            pass

    def restore_t55xx_external(self):
        path=filedialog.askopenfilename(title='Select T55xx Dump',initialdir=self.backup_dir,filetypes=[('T55xx / Proxmark dumps','*.json *.bin *.dump'),('All files','*.*')])
        if path:self.restore_saved_dump(path,force_kind='t55xx')

    def _quote_pm3(self, value):
        return '"' + str(value).replace('\\','\\\\').replace('"','\\"') + '"'

    def _ndef_record(self, action, payload):
        """Return a bare NDEF message for the creator's supported record types."""
        if not payload:
            return b''
        if action == 'Plain Text':
            lang=b'en'; txt=payload.encode('utf-8'); body=bytes([len(lang)])+lang+txt
            if len(body) > 255: return b''
            return bytes([0xD1,0x01,len(body),0x54])+body
        if action == 'Contact Card':
            typ=b'text/vcard'; body=payload.encode('utf-8')
            if len(body) > 255: return b''
            return bytes([0xD2,len(typ),len(body)])+typ+body
        # URI well-known record. Compress common URI prefixes when possible.
        prefixes=[('https://www.',0x02),('http://www.',0x01),('https://',0x04),('http://',0x03),('tel:',0x05),('mailto:',0x06)]
        code=0; rest=payload
        for pre,c in prefixes:
            if payload.lower().startswith(pre): code=c; rest=payload[len(pre):]; break
        body=bytes([code])+rest.encode('utf-8')
        if len(body) > 255: return b''
        return bytes([0xD1,0x01,len(body),0x55])+body

    def _ndef_tlv(self, msg):
        if len(msg) < 0xFF: return bytes([0x03,len(msg)])+msg+b'\xFE'
        if len(msg) <= 0xFFFF: return b'\x03\xFF'+len(msg).to_bytes(2,'big')+msg+b'\xFE'
        return b''

    def open_nfc_creator(self):
        """Show NFC Tag Studio inside the permanent right-hand workspace."""
        self._clear_right_page()
        self._select_page('studio'); self._activity_feedback('NFC Tag Studio','NFC Tag Studio ready.',False,None)
        if not self.connected:
            messagebox.showwarning(APP,'Connect to the Proxmark3 first.'); return
        if self.creator_window and self.creator_window.winfo_exists(): return
        try: self.home_body.pack_forget()
        except Exception: pass
        win=ttk.Frame(self.main_body,style='Card.TFrame')
        self.creator_window=win; win.pack(fill='both',expand=True)
        studio=ttk.Frame(win,style='Card.TFrame',padding=(12,2)); studio.pack(fill='both',expand=True)
        win._studio_frame=studio

        head=ttk.Frame(studio,style='Card.TFrame'); head.pack(fill='x',pady=(0,0))
        ttk.Label(head,text='NFC TAG STUDIO',style='TagTitle.TLabel',font=('Sans',18,'bold')).pack(side='left')
        ttk.Label(head,text='Create • Inspect • Write • Verify',style='Muted.TLabel').pack(side='left',padx=14)
        ttk.Label(studio,text='Build standard NDEF content, check the target tag, then write and verify it in this workspace.',style='Muted.TLabel').pack(anchor='w',pady=(0,2))

        wrap=ttk.Frame(studio,style='Card.TFrame')
        wrap.pack(fill='both',expand=True)

        kind=tk.StringVar(value='Website / URL')
        # Compact two-column Studio: content/preview on the left, target tools on the right.
        # Content type is a dropdown so navigation never competes with the actual work area.
        topgrid=ttk.Frame(wrap,style='Card.TFrame'); topgrid.pack(fill='both',expand=True,pady=(0,2))
        topgrid.columnconfigure(0,weight=3,minsize=560); topgrid.columnconfigure(1,weight=2,minsize=360); topgrid.rowconfigure(0,weight=1)
        editor=ttk.Frame(topgrid,style='Card.TFrame'); editor.grid(row=0,column=0,sticky='nsew',padx=(0,8))
        right=ttk.Frame(editor,style='Card.TFrame'); right.pack(fill='both',expand=True)
        types=['Website / URL','Contact / vCard','Phone','Email','SMS','Wi-Fi','Plain Text','Custom NDEF']
        content_head=ttk.Frame(right,style='Card2.TFrame',padding=(9,4)); content_head.pack(fill='x',pady=(0,4))
        ttk.Label(content_head,text='1. CONTENT',background=self.PANEL2,foreground='#79bdff',font=('Sans',10,'bold')).pack(anchor='w')
        type_row=ttk.Frame(content_head,style='Card2.TFrame'); type_row.pack(fill='x',pady=(3,0))
        ttk.Label(type_row,text='Content type:',width=16,background=self.PANEL2,foreground=self.TEXT).pack(side='left')
        type_combo=ttk.Combobox(type_row,textvariable=kind,state='readonly',values=types,width=28); type_combo.pack(side='left',fill='x',expand=True)
        def choose(k):
            kind.set(k); refresh()
        type_combo.bind('<<ComboboxSelected>>',lambda e:refresh())

        formcard=ttk.Frame(right,style='Card2.TFrame',padding=(8,5)); formcard.pack(fill='x')
        entries={}
        def field(key,label,default='',secret=False):
            f=ttk.Frame(formcard,style='Card2.TFrame'); f.pack(fill='x',pady=1)
            ttk.Label(f,text=label,width=17,background=self.PANEL2,foreground=self.TEXT).pack(side='left')
            v=tk.StringVar(value=default); e=ttk.Entry(f,textvariable=v,show='•' if secret else '')
            e.pack(side='left',fill='x',expand=True); entries[key]=(f,v,e); return v
        url=field('url','Website','https://prolocksuk.co.uk'); name=field('name','Name'); company=field('company','Company','Pro Locks UK')
        phone=field('phone','Phone'); email=field('email','Email'); subject=field('subject','Subject'); message=field('message','Message / Text')
        ssid=field('ssid','Wi-Fi name (SSID)'); wifi_pass=field('wifi_pass','Wi-Fi password','',True); custom=field('custom','NDEF hex')
        secrow=ttk.Frame(formcard,style='Card2.TFrame'); secrow.pack(fill='x',pady=1); ttk.Label(secrow,text='Wi-Fi security',width=17,background=self.PANEL2,foreground=self.TEXT).pack(side='left')
        wifi_sec=tk.StringVar(value='WPA2'); ttk.Combobox(secrow,textvariable=wifi_sec,state='readonly',values=['WPA2','WPA','Open'],width=16).pack(side='left')

        templatebar=ttk.Frame(right,style='Card.TFrame'); templatebar.pack(fill='x',pady=(2,1))
        ttk.Label(templatebar,text='Saved template').pack(side='left'); template=tk.StringVar(value='')
        tcombo=ttk.Combobox(templatebar,textvariable=template,state='readonly',values=sorted(self.templates),width=28); tcombo.pack(side='left',padx=6)
        ttk.Button(templatebar,text='LOAD',style='Action.TButton',command=lambda:load_template()).pack(side='left')
        ttk.Button(templatebar,text='SAVE TEMPLATE',style='Action.TButton',command=lambda:save_template()).pack(side='left',padx=4)

        apple_android=tk.BooleanVar(value=True)
        compatbar=ttk.Frame(right,style='Card.TFrame'); compatbar.pack(fill='x',pady=(0,1))
        ttk.Checkbutton(compatbar,text='Apple + Android compatible — standard NFC Forum NDEF',variable=apple_android,command=lambda:update_preview()).pack(anchor='w')

        preview_frame=ttk.Frame(right,style='Card2.TFrame',padding=(8,4)); preview_frame.pack(fill='x',pady=(1,1))
        preview_head=ttk.Frame(preview_frame,style='Card2.TFrame'); preview_head.pack(fill='x')
        ttk.Label(preview_head,text='2. PREVIEW',background=self.PANEL2,foreground='#c9def6',font=('Sans',9,'bold')).pack(side='left')
        copy_preview_btn=ttk.Button(preview_head,text='COPY CONTENT',style='Action.TButton'); copy_preview_btn.pack(side='right')
        preview=tk.Text(preview_frame,height=1,wrap='word',background=self.PANEL2,foreground='#c9def6',insertbackground=self.TEXT,relief='flat',borderwidth=0,font=('Sans',10))
        preview.pack(fill='x',pady=(2,0)); preview.config(state='disabled')
        meter=ttk.Label(right,text='Estimated payload: 0 bytes • Target capacity: unknown',style='Muted.TLabel'); meter.pack(anchor='w',pady=(1,0))
        decoded_state={'text':'','kind':None,'fields':{}}
        def set_preview(text):
            preview.config(state='normal'); preview.delete('1.0','end'); preview.insert('1.0',text); preview.config(state='disabled')
        def copy_preview():
            txt=preview.get('1.0','end-1c').strip()
            if txt:self.clipboard_clear();self.clipboard_append(txt);status.config(text='✓ Preview copied to clipboard.')
        copy_preview_btn.config(command=copy_preview)

        target={'kind':None,'name':None,'uid':'','capacity':None,'formatted':None,'writable':None,'raw':''}

        # Compact target card: identity/status and everyday actions live together.
        # This avoids another full-width heading block and keeps PROGRAM TAG visible.
        target_actions=ttk.Frame(topgrid,style='Card2.TFrame',padding=(10,8)); target_actions.grid(row=0,column=1,sticky='nsew')
        target_head=ttk.Frame(target_actions,style='Card2.TFrame'); target_head.pack(fill='x',pady=(0,3))
        targetbox=ttk.Label(target_head,text='3. TARGET TAG  •  Not checked',background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')); targetbox.pack(side='left')
        status=ttk.Label(target_head,text='Place a tag on the HF antenna.',background=self.PANEL2,foreground=self.MUTED); status.pack(side='right')
        target_row=ttk.Frame(target_actions,style='Card2.TFrame'); target_row.pack(fill='x')
        taginfo_btn=ttk.Button(target_row,text='TAG INFO',style='Action.TButton')
        read_btn=ttk.Button(target_row,text='READ NDEF',style='Action.TButton',state='disabled')
        erase_btn=ttk.Button(target_row,text='ERASE NDEF',style='Action.TButton',state='disabled')
        format_btn=ttk.Button(target_row,text='FORMAT NDEF',style='Action.TButton',state='disabled')
        verify_btn=ttk.Button(target_row,text='VERIFY',style='Action.TButton',state='disabled')
        for i,b in enumerate((taginfo_btn,read_btn,erase_btn,format_btn,verify_btn)):
            b.grid(row=i,column=0,sticky='ew',pady=(0 if i==0 else 4,0))
        target_row.columnconfigure(0,weight=1)

        advanced_box=ttk.Frame(target_actions,style='Card2.TFrame')
        advanced_row=ttk.Frame(advanced_box,style='Card.TFrame'); advanced_row.pack(fill='x')
        backup_btn=ttk.Button(advanced_row,text='DUMP / BACKUP',style='Action.TButton',state='disabled')
        restore_btn=ttk.Button(advanced_row,text='RESTORE DUMP',style='Action.TButton',state='disabled')
        lock_btn=ttk.Button(advanced_row,text='MAKE READ-ONLY / LOCK',style='Danger.TButton',state='disabled')
        for i,b in enumerate((backup_btn,restore_btn,lock_btn)):
            b.grid(row=0,column=i,sticky='ew',padx=(0 if i==0 else 4,0)); advanced_row.columnconfigure(i,weight=1)
        advanced_note=ttk.Label(advanced_box,text='Backup/restore are enabled only for compatible detected tags. Permanent locking stays disabled unless Workshop can prove a safe model-specific route.',style='Muted.TLabel',wraplength=1050); advanced_note.pack(anchor='w',pady=(4,0))
        advanced_open={'v':False}
        def toggle_advanced_tools():
            advanced_open['v']=not advanced_open['v']
            if advanced_open['v']:
                advanced_box.pack(fill='x',pady=(2,2)); advanced_toggle.config(text='ADVANCED TAG TOOLS ▴')
            else:
                advanced_box.pack_forget(); advanced_toggle.config(text='ADVANCED TAG TOOLS ▾')
        advanced_toggle=ttk.Button(target_actions,text='ADVANCED TAG TOOLS ▾',style='Action.TButton',command=toggle_advanced_tools)
        advanced_toggle.pack(anchor='e',pady=(0,1))

        # Programming mode stays permanently visible above progress so Batch Mode
        # can never be pushed below the bottom edge on a 1366x768 desktop.
        batchbox=ttk.Frame(wrap,style='Card2.TFrame',padding=(8,3)); batchbox.pack(fill='x',pady=(1,2))
        mode_row=ttk.Frame(batchbox,style='Card2.TFrame'); mode_row.pack(fill='x')
        ttk.Label(mode_row,text='4. PROGRAM',background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')).pack(side='left')
        batch=tk.BooleanVar(value=False)
        ttk.Radiobutton(mode_row,text='SINGLE TAG',variable=batch,value=False).pack(side='left',padx=(18,6))
        ttk.Radiobutton(mode_row,text='BATCH MODE',variable=batch,value=True).pack(side='left',padx=6)
        batch_controls=ttk.Frame(batchbox,style='Card2.TFrame')
        ttk.Label(batch_controls,text='Number of tags:',background=self.PANEL2,foreground=self.TEXT).pack(side='left')
        batch_total=tk.IntVar(value=10)
        batch_spin=tk.Spinbox(batch_controls,from_=1,to=999,textvariable=batch_total,width=5,bg='#061827',fg=self.TEXT,insertbackground='white',buttonbackground='#0d2944'); batch_spin.pack(side='left',padx=(6,14))
        batch_done=tk.IntVar(value=0)
        batch_label=ttk.Label(batch_controls,text='TAG 1 OF 10  •  0 complete',background=self.PANEL2,foreground=self.MUTED); batch_label.pack(side='left')
        reset_batch_btn=ttk.Button(batch_controls,text='RESET BATCH',style='Action.TButton'); reset_batch_btn.pack(side='right')
        batch_popup={'win':None,'count':None,'stage':None,'progress':None,'detail':None,'next':None}

        # All NFC work stays inside this window. Raw PM3 output continues to the
        # technical log, while this compact panel shows the useful live state.
        progressbox=ttk.Frame(wrap,style='Card2.TFrame',padding=(8,5))
        progress_title=ttk.Label(progressbox,text='READY',background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')); progress_title.pack(anchor='w')
        progress=ttk.Progressbar(progressbox,mode='determinate',maximum=100,style='Horizontal.TProgressbar'); progress.pack(fill='x',pady=(3,2))
        progress_stage=ttk.Label(progressbox,text='Waiting for a tag.',background=self.PANEL2,foreground=self.MUTED); progress_stage.pack(anchor='w')

        # Keep the primary action inside the always-visible programming panel.
        # This prevents both Single Tag and Batch Start from being pushed below
        # the bottom edge on 1366x768 desktops.
        auto_btn=ttk.Button(batchbox,text='PROGRAM TAG  •  DETECT → PREPARE → WRITE → VERIFY',style='Primary.TButton')
        auto_btn.pack(fill='x',ipady=4,pady=(3,0))
        def refresh_batch_ui(*_):
            total=max(1,int(batch_total.get() or 1)); done=min(batch_done.get(),total)
            if batch.get():
                batch_controls.pack(fill='x',pady=(6,0))
                nxt=min(done+1,total)
                batch_label.config(text=f'TAG {nxt} OF {total}  •  {done} complete')
                if done < total: auto_btn.config(text='START BATCH',state='normal')
            else:
                batch_controls.pack_forget()
                if str(auto_btn['state']) != 'disabled': auto_btn.config(text='PROGRAM TAG  •  DETECT → PREPARE → WRITE → VERIFY')

        def close_batch_popup():
            w=batch_popup.get('win')
            if w and w.winfo_exists():
                try:w.grab_release()
                except:pass
                w.destroy()
            batch_popup['win']=None

        def update_batch_popup(pct=None,title=None,detail=None):
            w=batch_popup.get('win')
            if not w or not w.winfo_exists(): return
            total=max(1,int(batch_total.get() or 1)); done=min(batch_done.get(),total); cur=min(done+1,total)
            batch_popup['count'].config(text=f'{cur} OF {total}' if done < total else f'{total} OF {total}')
            if pct is not None: batch_popup['progress']['value']=pct
            if title is not None: batch_popup['stage'].config(text=title)
            if detail is not None: batch_popup['detail'].config(text=detail)

        def open_batch_popup():
            if not batch.get(): return
            try: total=max(1,int(batch_total.get() or 1))
            except: total=1; batch_total.set(1)
            if batch_done.get()>=total: batch_done.set(0)
            close_batch_popup()
            w=tk.Toplevel(win); batch_popup['win']=w; w.title('Batch Programming — Proxmark3 Workshop'); w.configure(bg=self.BG); w.resizable(False,False)
            ww,hh=720,570; sw=w.winfo_screenwidth(); sh=w.winfo_screenheight(); w.geometry(f'{ww}x{hh}+{max(0,(sw-ww)//2)}+{max(0,(sh-hh)//2)}')
            w.transient(win); w.grab_set(); w.protocol('WM_DELETE_WINDOW',close_batch_popup)
            outer=ttk.Frame(w,style='Card.TFrame',padding=22); outer.pack(fill='both',expand=True)
            ttk.Label(outer,text='BATCH PROGRAMMING',style='TagTitle.TLabel',font=('Sans',18,'bold')).pack(anchor='center',pady=(4,0))
            ttk.Label(outer,text='Programming multiple NFC tags',style='Muted.TLabel').pack(anchor='center',pady=(0,12))
            count=ttk.Label(outer,text=f'1 OF {total}',style='TagTitle.TLabel',font=('Sans',38,'bold')); count.pack(anchor='center',pady=(4,2)); batch_popup['count']=count
            ttk.Label(outer,text='Place the current tag on the Proxmark3 HF antenna',style='Card.TLabel',font=('Sans',12,'bold')).pack(anchor='center')
            ttk.Label(outer,text='Detect  →  Prepare  →  Write  →  Verify',style='Muted.TLabel',font=('Sans',11)).pack(anchor='center',pady=(12,8))
            pr=ttk.Progressbar(outer,mode='determinate',maximum=100,style='Horizontal.TProgressbar'); pr.pack(fill='x',pady=(4,3)); batch_popup['progress']=pr
            stage=ttk.Label(outer,text='READY',style='CardTitle.TLabel',font=('Sans',11,'bold')); stage.pack(anchor='center',pady=(5,1)); batch_popup['stage']=stage
            detail=ttk.Label(outer,text='Place Tag 1 on the antenna, then start programming.',style='Muted.TLabel',wraplength=640,justify='center'); detail.pack(anchor='center',pady=(0,12)); batch_popup['detail']=detail
            nxt=ttk.Button(outer,text='PROGRAM TAG 1',style='Primary.TButton',command=lambda:auto_program()); nxt.pack(fill='x',ipady=10,pady=(8,6)); batch_popup['next']=nxt
            ttk.Button(outer,text='CANCEL / CLOSE BATCH',style='Action.TButton',command=close_batch_popup).pack(ipadx=24,ipady=4,pady=(4,0))
            w.lift(); w.focus_force()

        def reset_batch():
            close_batch_popup(); batch_done.set(0); refresh_batch_ui(); targetbox.config(text='TARGET  •  Not checked'); status.config(text='Batch reset. Place Tag 1 on the HF antenna.')
        reset_batch_btn.config(command=reset_batch)
        batch.trace_add('write',refresh_batch_ui); batch_total.trace_add('write',refresh_batch_ui)

        manual_toggle=ttk.Button(batchbox,text='MANUAL TOOLS ▾',style='Action.TButton')
        manual_toggle.pack(anchor='e',pady=(1,0))
        steps=ttk.Frame(wrap,style='Card.TFrame')
        check_btn=taginfo_btn
        write_btn=ttk.Button(steps,text='WRITE + VERIFY',style='Action.TButton',state='disabled'); write_btn.grid(row=0,column=0,sticky='ew',padx=(0,4),pady=2)
        duplicate_btn=ttk.Button(steps,text='DUPLICATE TO ANOTHER TAG',style='Action.TButton',state='disabled'); duplicate_btn.grid(row=0,column=1,sticky='ew',padx=(4,0),pady=2)
        for i in range(2): steps.columnconfigure(i,weight=1)
        manage=ttk.Frame(wrap,style='Card.TFrame')
        reset_btn=ttk.Button(wrap,text='RESET TEST MIFARE CLASSIC TO STANDARD',style='Danger.TButton',state='disabled')
        manual_open={'v':False}
        def toggle_manual_tools():
            manual_open['v']=not manual_open['v']
            if manual_open['v']:
                steps.pack(fill='x',pady=(2,0))
                reset_btn.pack(fill='x',pady=(2,0))
                manual_toggle.config(text='MANUAL TOOLS ▴')
            else:
                steps.pack_forget(); reset_btn.pack_forget()
                manual_toggle.config(text='MANUAL TOOLS ▾')
        manual_toggle.config(command=toggle_manual_tools)
        refresh_batch_ui()

        def wifi_wsc():
            # Wi-Fi Simple Configuration credential attributes carried in the standard WSC MIME record.
            def a(t,b): return t.to_bytes(2,'big')+len(b).to_bytes(2,'big')+b
            auth={'Open':0x0001,'WPA':0x0008,'WPA2':0x0020}.get(wifi_sec.get(),0x0020)
            enc=0x0001 if wifi_sec.get()=='Open' else 0x0008
            cred=a(0x1045,ssid.get().encode())+a(0x1003,auth.to_bytes(2,'big'))+a(0x100F,enc.to_bytes(2,'big'))+a(0x1027,wifi_pass.get().encode())
            return a(0x100E,cred)
        def current_payload():
            k=kind.get()
            if k=='Website / URL': return url.get().strip()
            if k=='Phone': return 'tel:'+phone.get().strip()
            if k=='Email':
                uri='mailto:'+email.get().strip(); qs=[]
                if subject.get().strip(): qs.append('subject='+quote(subject.get().strip()))
                if message.get().strip(): qs.append('body='+quote(message.get().strip()))
                return uri+('?'+'&'.join(qs) if qs else '')
            if k=='SMS': return 'sms:'+phone.get().strip()+(('?body='+quote(message.get().strip())) if message.get().strip() else '')
            if k=='Plain Text': return message.get().strip()
            if k=='Contact / vCard':
                lines=['BEGIN:VCARD','VERSION:3.0']
                if name.get().strip(): lines.append('FN:'+name.get().strip())
                if company.get().strip(): lines.append('ORG:'+company.get().strip())
                if phone.get().strip(): lines.append('TEL:'+phone.get().strip())
                if email.get().strip(): lines.append('EMAIL:'+email.get().strip())
                if url.get().strip(): lines.append('URL:'+url.get().strip())
                lines.append('END:VCARD'); return '\r\n'.join(lines)
            if k=='Wi-Fi': return f'SSID: {ssid.get()}  •  Security: {wifi_sec.get()}  •  Password: '+('••••••••' if wifi_pass.get() else '(none)')
            if k=='Custom NDEF': return custom.get().strip().replace(' ','')
            return ''
        def make_message():
            k=kind.get()
            if k=='Custom NDEF':
                try:return bytes.fromhex(custom.get().strip().replace(' ',''))
                except:return b''
            if k=='Wi-Fi':
                typ=b'application/vnd.wfa.wsc'; body=wifi_wsc()
                if len(body)<=255:return bytes([0xD2,len(typ),len(body)])+typ+body
                return bytes([0xC2,len(typ)])+len(body).to_bytes(4,'big')+typ+body
            old={'Website / URL':'Open Website','Contact / vCard':'Contact Card','Phone':'Phone Number','Email':'Email','SMS':'SMS','Plain Text':'Plain Text'}.get(k,k)
            return self._ndef_record(old,current_payload())
        def update_preview(*_):
            p=current_payload(); msg=make_message(); n=len(self._ndef_tlv(msg) if msg else b'')
            title='CONTENT PREVIEW'
            set_preview(title+'\n'+(p if p else '(enter content)'))
            cap=target.get('capacity'); fit=''
            if cap: fit='  ✓ FITS' if n<=cap else '  ✗ TOO LARGE'
            compat=' • Apple + Android NDEF' if apple_android.get() else ''
            chip=target.get('name') or 'No target checked'; writable=('Writable' if target.get('writable') is True else ('Read-only' if target.get('writable') is False else 'Write state unknown'))
            ndefstate=('NDEF formatted' if target.get('formatted') is True else ('NDEF not confirmed' if target.get('formatted') is False else 'NDEF state unknown'))
            meter.config(text=f'Estimated encoded size: {n} bytes • {chip} • Capacity: {cap if cap else "unknown"}{fit} • {ndefstate} • {writable}{compat}')
        def refresh(*_):
            show={'Website / URL':['url'],'Contact / vCard':['name','company','phone','email','url'],'Phone':['phone'],'Email':['email','subject','message'],'SMS':['phone','message'],'Wi-Fi':['ssid','wifi_pass'],'Plain Text':['message'],'Custom NDEF':['custom']}[kind.get()]
            for key,(f,v,e) in entries.items():
                if key in show:f.pack(fill='x',pady=1)
                else:f.pack_forget()
            if kind.get()=='Wi-Fi':secrow.pack(fill='x',pady=1)
            else:secrow.pack_forget()
            update_preview()
        for _,v,_ in entries.values(): v.trace_add('write',update_preview)
        wifi_sec.trace_add('write',update_preview)

        def edit_decoded():
            d=decoded_state
            if not d.get('kind'): return
            choose(d['kind'])
            for key,val in d.get('fields',{}).items():
                if key in entries: entries[key][1].set(val)
            status.config(text='✓ Existing NDEF loaded into the editor. Make changes, then CHECK TARGET and WRITE + VERIFY.')
            update_preview()

        def load_template():
            d=self.templates.get(template.get());
            if not d:return
            k=d.get('kind','Website / URL'); aliases={'Open Website':'Website / URL','Contact Card':'Contact / vCard','Phone Number':'Phone'}; choose(aliases.get(k,k))
            for key,(_,v,_) in entries.items(): v.set(d.get(key,''))
            wifi_sec.set(d.get('wifi_sec','WPA2'))
            apple_android.set(bool(d.get('apple_android',True)))
            update_preview()
        def save_template():
            n=simpledialog.askstring(APP,'Template name:',parent=self)
            if not n:return
            self.templates[n]={'kind':kind.get(),'wifi_sec':wifi_sec.get(),'apple_android':bool(apple_android.get()),**{k:v.get() for k,(_,v,_) in entries.items()}}
            self._save_json(self.templates_path,self.templates); tcombo.config(values=sorted(self.templates)); template.set(n); status.config(text='✓ Template saved.')

        def set_progress(pct,title,detail):
            if not progressbox.winfo_manager(): progressbox.pack(fill='x',pady=(2,3),before=batchbox)
            progress['value']=pct; progress_title.config(text=title); progress_stage.config(text=detail)
            self._activity_feedback(title,detail,working=(pct < 100),success=(True if pct >= 100 else None))
        def creator_done(text,ok=False):
            status.config(text=text); auto_btn.config(state='normal'); check_btn.config(state='normal')
            if ok:
                set_progress(100,'COMPLETE & VERIFIED','The tag was written and read back successfully.')
            if batch.get():
                total=max(1,int(batch_total.get() or 1))
                if ok:
                    done=batch_done.get()+1; batch_done.set(done); batch_label.config(text=f'TAG {min(done+1,total)} OF {total}  •  {done} complete')
                    target.update({'kind':None,'name':None,'uid':'','capacity':None,'formatted':None,'writable':None,'raw':''})
                    for b in (format_btn,write_btn,erase_btn,reset_btn,read_btn,verify_btn,duplicate_btn,backup_btn,restore_btn,lock_btn): b.config(state='disabled')
                    if done < total:
                        targetbox.config(text=f'BATCH  •  Tag {done} complete — place tag {done+1} of {total}')
                        status.config(text=f'✓ Tag {done} of {total} complete and verified. Replace it, then press PROGRAM NEXT TAG.')
                        auto_btn.config(text='START BATCH')
                        set_progress(0,'READY FOR NEXT TAG',f'Place tag {done+1} of {total} on the antenna.')
                        update_batch_popup(0,'✓ TAG VERIFIED',f'Remove Tag {done}, place Tag {done+1} on the antenna, then press PROGRAM NEXT TAG.')
                        if batch_popup.get('next'): batch_popup['next'].config(text='PROGRAM NEXT TAG',state='normal')
                    else:
                        targetbox.config(text=f'BATCH COMPLETE  •  {done}/{total} tags verified'); status.config(text=f'✓ BATCH COMPLETE — {done}/{total} tags successfully programmed and verified.'); auto_btn.config(text='BATCH COMPLETE',state='disabled')
                        update_batch_popup(100,'✓ BATCH COMPLETE',f'{done} OF {total} successfully programmed and verified.')
                        if batch_popup.get('next'): batch_popup['next'].config(text='FINISH',state='normal',command=close_batch_popup)
                else:
                    cur=min(batch_done.get()+1,total)
                    update_batch_popup(0,f'⚠ TAG {cur} FAILED — NOT COUNTED','Replace or reposition the tag, then retry. The batch counter has not advanced.')
                    if batch_popup.get('next'): batch_popup['next'].config(text=f'RETRY TAG {cur}',state='normal')
        def check_target():
            status.config(text='Detecting target…'); check_btn.config(state='disabled')
            for b in (format_btn,write_btn,erase_btn,reset_btn,read_btn,verify_btn,duplicate_btn,backup_btn,restore_btn,lock_btn): b.config(state='disabled')
            def worker():
                try:
                    out=self.pm.execute('hf search'); tag=self.parse_tag(out)
                    if not tag:self.q.put(('creator_result',('No supported HF tag detected. Keep the tag on the HF antenna and retry.',None)));return
                    info=''; cap=None
                    if tag['kind']=='mfu':
                        info=self.pm.execute('hf mfu info'); low=ANSI_RE.sub('',info).lower(); nm=(tag.get('name','')+' '+low)
                        if 'ntag213' in nm:cap=144
                        elif 'ntag215' in nm:cap=504
                        elif 'ntag216' in nm:cap=888
                    elif tag['kind']=='mfc': info=self.pm.execute('hf mf info'); cap=716
                    elif tag['kind']=='mfp': info=self.pm.execute('hf mfp info')
                    elif tag['kind']=='type4a': info=self.pm.execute('hf 14a info')
                    else:self.q.put(('creator_result',(f'{tag["name"]} detected, but this technology is not enabled as a generic NDEF writing target.',None)));return
                    profile={'kind':tag['kind'],'name':tag['name'],'uid':tag.get('uid',''),'capacity':cap,'formatted':None,'writable':True,'raw':info}
                    self.q.put(('creator_result',(f'✓ {tag["name"]} detected • UID {tag.get("uid") or "n/a"}',profile)))
                except Exception as e:self.q.put(('creator_result',('Target check failed: '+str(e),None)))
            threading.Thread(target=worker,daemon=True).start()
        check_btn.config(command=check_target)
        def format_target():
            if not target['kind']:return
            if not messagebox.askyesno(APP,'Format this tag for NFC/NDEF? This changes tag data.',parent=win):return
            cmd={'mfu':'hf mfu ndefformat','mfc':'hf mf ndefformat','mfp':'hf mfp ndefformat','type4a':'hf 14a ndefformat'}.get(target['kind'])
            def worker():
                try:
                    self.pm.execute(cmd)
                    readcmd={'mfu':'hf mfu ndefread','mfc':'hf mf ndefread','mfp':'hf mfp ndefread','type4a':'hf 14a ndefread'}.get(target.get('kind'))
                    if readcmd:self.pm.execute(readcmd)
                    target['formatted']=True
                    self.q.put(('creator_op',('✓ NDEF format complete and tag responded to read-back.',True,'format')))
                except Exception as e:self.q.put(('creator_op',('✗ Format/verification failed: '+str(e),False,'format')))
            threading.Thread(target=worker,daemon=True).start()
        format_btn.config(command=format_target)
        def build_cmd():
            msg=make_message()
            if not msg:return None
            if target['kind']=='mfu':return 'hf mfu ndefwrite -d '+msg.hex().upper()
            tlv=self._ndef_tlv(msg)
            return {'mfc':'hf mf ndefwrite -d ','mfp':'hf mfp ndefwrite -d ','type4a':'hf 14a ndefwrite -d '}.get(target['kind'],'')+tlv.hex().upper() if tlv else None
        def auto_program():
            msg=make_message()
            if not msg:
                messagebox.showwarning(APP,'Enter valid NFC content first.',parent=win); return
            auto_btn.config(state='disabled'); check_btn.config(state='disabled')
            if batch.get() and batch_popup.get('next'): batch_popup['next'].config(state='disabled')
            for b in (format_btn,write_btn,erase_btn,reset_btn,read_btn,verify_btn,duplicate_btn,backup_btn,restore_btn,lock_btn): b.config(state='disabled')
            status.config(text='Automatic NFC programming in progress — keep the tag still.')
            set_progress(5,'1 / 5  DETECTING TAG','Looking for an NFC-compatible HF tag…')
            def report(pct,title,detail): self.q.put(('creator_progress',(pct,title,detail)))
            def worker():
                try:
                    report(10,'1 / 5  DETECTING TAG','Identifying chip and UID…')
                    out=self.pm.execute('hf search'); tag=self.parse_tag(out)
                    if not tag: raise RuntimeError('No supported HF/NFC tag detected.')
                    info=''; cap=None
                    if tag['kind']=='mfu':
                        info=self.pm.execute('hf mfu info'); low=ANSI_RE.sub('',info).lower(); nm=(tag.get('name','')+' '+low)
                        if 'ntag213' in nm:cap=144
                        elif 'ntag215' in nm:cap=504
                        elif 'ntag216' in nm:cap=888
                    elif tag['kind']=='mfc': info=self.pm.execute('hf mf info'); cap=716
                    elif tag['kind']=='mfp': info=self.pm.execute('hf mfp info')
                    elif tag['kind']=='type4a': info=self.pm.execute('hf 14a info')
                    else: raise RuntimeError(f'{tag.get("name","This tag")} is not supported as a generic NDEF writing target.')
                    profile={'kind':tag['kind'],'name':tag['name'],'uid':tag.get('uid',''),'capacity':cap,'formatted':None,'writable':True,'raw':info}
                    target.update(profile); self.q.put(('creator_profile',profile))
                    report(28,'2 / 5  CHECKING NDEF','Checking whether the tag is already NFC Forum formatted…')
                    readcmd={'mfu':'hf mfu ndefread','mfc':'hf mf ndefread','mfp':'hf mfp ndefread','type4a':'hf 14a ndefread'}[tag['kind']]
                    formatted=True
                    try:self.pm.execute(readcmd)
                    except Exception:formatted=False
                    target['formatted']=formatted
                    if not formatted:
                        report(42,'3 / 5  FORMATTING NDEF','Preparing the tag as a standard NFC Forum NDEF tag…')
                        fmt={'mfu':'hf mfu ndefformat','mfc':'hf mf ndefformat','mfp':'hf mfp ndefformat','type4a':'hf 14a ndefformat'}[tag['kind']]
                        self.pm.execute(fmt); target['formatted']=True
                    else: report(42,'3 / 5  NDEF READY','Tag is already NDEF formatted — no format needed.')
                    cmd=build_cmd(); msg2=make_message()
                    if not cmd or not msg2: raise RuntimeError('The NFC content could not be encoded.')
                    est=len(self._ndef_tlv(msg2) if tag['kind']!='mfu' else msg2)
                    if cap and est>cap: raise RuntimeError(f'Encoded message is {est} bytes but this tag has about {cap} bytes available.')
                    report(62,'4 / 5  WRITING NFC CONTENT','Writing the selected content…'); self.pm.execute(cmd)
                    report(82,'5 / 5  VERIFYING','Reading the tag back and checking the result…'); verify=self.pm.execute(readcmd)
                    clean=ANSI_RE.sub('',verify or '').strip()
                    if not clean: raise RuntimeError('Read-back returned no NDEF data.')
                    expected=current_payload().strip()
                    # For human-readable standard records, require the payload to appear in read-back.
                    if expected and kind.get() in ('Website / URL','Phone','Email','SMS','Plain Text'):
                        probe=expected
                        if kind.get()=='Phone': probe=phone.get().strip()
                        elif kind.get()=='Email': probe=email.get().strip()
                        elif kind.get()=='SMS': probe=phone.get().strip()
                        if probe and probe.lower() not in clean.lower(): raise RuntimeError('Read-back completed, but the expected content was not found. Verification failed.')
                    self._record_history('NFC auto program',target,True); self.q.put(('creator_op',('✓ NFC tag programmed and verified successfully.',True,'write')))
                except Exception as e:
                    self._record_history('NFC auto program',target,False); self.q.put(('creator_progress',(0,'PROGRAMMING STOPPED',str(e)))); self.q.put(('creator_op',('✗ Automatic programming stopped: '+str(e),False,'auto')))
            threading.Thread(target=worker,daemon=True).start()
        auto_btn.config(command=lambda: open_batch_popup() if batch.get() else auto_program())

        def write_target():
            if not target['kind']:return
            cmd=build_cmd(); msg=make_message()
            if not cmd or not msg:messagebox.showwarning(APP,'Enter valid content first.');return
            est=len(self._ndef_tlv(msg) if target['kind']!='mfu' else msg)
            if target.get('capacity') and est>target['capacity']:messagebox.showerror(APP,'The encoded NDEF message is too large for this tag.');return
            if not messagebox.askyesno(APP,'Write this NFC content and verify it by reading it back?',parent=win):return
            uid=target.get('uid',''); readcmd={'mfu':'hf mfu ndefread','mfc':'hf mf ndefread','mfp':'hf mfp ndefread','type4a':'hf 14a ndefread'}[target['kind']]
            status.config(text='Re-checking target → writing → reading back…')
            def worker():
                try:
                    t2=self.parse_tag(self.pm.execute('hf search'))
                    if uid and (not t2 or t2.get('uid','').strip()!=uid.strip()):raise RuntimeError('Target tag changed. Write cancelled.')
                    self.pm.execute(cmd); verify=self.pm.execute(readcmd)
                    if not ANSI_RE.sub('',verify).strip():raise RuntimeError('Read-back returned no NDEF data.')
                    self._record_history('NFC write',target,True);self.q.put(('creator_op',('✓ NFC content written and read back successfully.',True,'write')))
                except Exception as e:self._record_history('NFC write',target,False);self.q.put(('creator_op',('✗ Write/verify failed: '+str(e),False,'write')))
            threading.Thread(target=worker,daemon=True).start()
        write_btn.config(command=write_target)
        def read_existing():
            cmd={'mfu':'hf mfu ndefread','mfc':'hf mf ndefread','mfp':'hf mfp ndefread','type4a':'hf 14a ndefread'}.get(target.get('kind'))
            if not cmd:return
            status.config(text='Reading existing NDEF…')
            def worker():
                try:self.q.put(('creator_read',self.pm.execute(cmd)))
                except Exception as e:self.q.put(('creator_op',('✗ NDEF read failed: '+str(e),False,'read')))
            threading.Thread(target=worker,daemon=True).start()
        read_btn.config(command=read_existing)
        def erase_ndef():
            cmd={'mfu':'hf mfu ndefwrite -d 0300FE','mfc':'hf mf ndefwrite -d 0300FE','mfp':'hf mfp ndefwrite -d 0300FE','type4a':'hf 14a ndefwrite -d 0300FE'}.get(target.get('kind'))
            if not cmd or not messagebox.askyesno(APP,'Erase only the NDEF message?\n\nThe physical tag remains usable. This does NOT perform a full tag wipe.',parent=win):return
            def worker():
                try:
                    self.pm.execute(cmd)
                    readcmd={'mfu':'hf mfu ndefread','mfc':'hf mf ndefread','mfp':'hf mfp ndefread','type4a':'hf 14a ndefread'}.get(target.get('kind'))
                    verify=self.pm.execute(readcmd) if readcmd else ''
                    self.q.put(('creator_op',('✓ NDEF content erased and tag read back successfully.',True,'erase')))
                except Exception as e:self.q.put(('creator_op',('✗ Erase/read-back verification failed: '+str(e),False,'erase')))
            threading.Thread(target=worker,daemon=True).start()
        erase_btn.config(command=erase_ndef)

        def verify_existing():
            cmd={'mfu':'hf mfu ndefread','mfc':'hf mf ndefread','mfp':'hf mfp ndefread','type4a':'hf 14a ndefread'}.get(target.get('kind'))
            if not cmd:return
            status.config(text='Reading NDEF and comparing it with the current Content Preview…')
            def worker():
                try:
                    raw=self.pm.execute(cmd); clean=ANSI_RE.sub('',raw or '').strip()
                    if not clean: raise RuntimeError('Read-back returned no NDEF data.')
                    expected=current_payload().strip(); probe=expected
                    if kind.get()=='Phone': probe=phone.get().strip()
                    elif kind.get()=='Email': probe=email.get().strip()
                    elif kind.get()=='SMS': probe=phone.get().strip()
                    comparable=kind.get() in ('Website / URL','Phone','Email','SMS','Plain Text') and bool(probe)
                    if comparable and probe.lower() not in clean.lower():
                        raise RuntimeError('The tag contains NDEF data, but it does not match the current Content Preview.')
                    self.q.put(('creator_op',('✓ NDEF read-back verified'+(' against the current preview.' if comparable else '. Tag contains readable NDEF data.'),True,'verify')))
                except Exception as e:self.q.put(('creator_op',('✗ Verification failed: '+str(e),False,'verify')))
            threading.Thread(target=worker,daemon=True).start()
        verify_btn.config(command=verify_existing)

        def backup_from_studio():
            if not target.get('kind'):return
            self.last_tag={'kind':target.get('kind'),'name':target.get('name'),'uid':target.get('uid','')}
            self.backup_current_tag()
        backup_btn.config(command=backup_from_studio)

        def restore_from_studio():
            if not target.get('kind'):return
            kind0=target.get('kind')
            if kind0 not in ('mfc','mfu'):
                messagebox.showinfo(APP,'Guided restore is enabled here only for MIFARE Classic and Ultralight/NTAG dumps.',parent=win);return
            path=filedialog.askopenfilename(title='Select compatible tag dump',initialdir=self.backup_dir,filetypes=[('Proxmark binary dump','*.bin'),('All files','*.*')],parent=win)
            if not path:return
            if not messagebox.askyesno(APP,f'Restore this dump to the detected {target.get("name") or "tag"}?\n\nThis writes tag memory and may overwrite existing data.',parent=win):return
            # Use the same guarded restore pipeline as Saved Dumps: fresh target detection,
            # write, physical read-back and byte comparison.  Do not have a second, weaker
            # Studio-only restore path that can report success from the write response alone.
            try: win.destroy()
            except Exception: pass
            self.restore_saved_dump(path,force_kind=kind0)
        restore_btn.config(command=restore_from_studio)

        def explain_lock():
            messagebox.showwarning(APP,'Permanent locking is model-specific. Workshop will not guess lock bytes. Use this only when an exact supported tag model has been identified and a safe model-specific lock route is available.',parent=win)
        lock_btn.config(command=explain_lock)
        def duplicate():
            # Preserve the composed content, but force a fresh target check so a different tag can be used safely.
            target.update({'kind':None,'name':None,'uid':'','capacity':None,'formatted':None,'writable':None,'raw':''});targetbox.config(text='TARGET  •  Present the destination tag')
            for b in (format_btn,write_btn,erase_btn,reset_btn,read_btn,verify_btn,duplicate_btn,backup_btn,restore_btn,lock_btn):b.config(state='disabled')
            check_btn.config(state='normal');status.config(text='Content retained. Present the destination tag, click CHECK TARGET, then WRITE + VERIFY.')
        duplicate_btn.config(command=duplicate)
        def reset_classic():
            if target.get('kind')!='mfc':return
            if not messagebox.askyesno(APP,'Wipe writable MIFARE Classic memory and restore default keys/access conditions? All application data will be lost.'):return
            if simpledialog.askstring(APP,'Type RESET to confirm:',parent=self)!='RESET':return
            uid=target.get('uid','');status.config(text='Re-checking target → resetting Classic → verifying…')
            def worker():
                try:
                    t2=self.parse_tag(self.pm.execute('hf search'))
                    if not t2 or t2.get('kind')!='mfc' or (uid and t2.get('uid','').strip()!=uid.strip()):raise RuntimeError('Target changed. Reset cancelled.')
                    self.pm.execute('hf mf wipe');self.pm.execute('hf mf info');self.q.put(('creator_op',('✓ MIFARE Classic reset complete.',True,'reset')))
                except Exception as e:self.q.put(('creator_op',('✗ Reset failed: '+str(e),False,'reset')))
            threading.Thread(target=worker,daemon=True).start()
        reset_btn.config(command=reset_classic)

        creator_state={'status':status,'target':target,'targetbox':targetbox,'check':check_btn,'format':format_btn,'write':write_btn,'auto':auto_btn,'erase':erase_btn,'reset':reset_btn,'read':read_btn,'verify':verify_btn,'duplicate':duplicate_btn,'backup':backup_btn,'restore':restore_btn,'lock':lock_btn,'meter':meter,'preview':preview,'progress':progress,'progress_title':progress_title,'progress_stage':progress_stage,'done':creator_done,'update_preview':update_preview,'set_preview':set_preview,'decoded':decoded_state,'kind':kind,'entries':entries,'choose':choose,'batch':batch,'batch_done':batch_done,'batch_total':batch_total,'batch_label':batch_label,'batch_popup':batch_popup,'update_batch_popup':update_batch_popup}
        studio._creator=creator_state
        win._creator=creator_state
        refresh()

    def _decode_ndef_output(self, raw):
        """Best-effort friendly decoder for RRG's human-readable NDEF output.
        Raw output is still retained in the technical log. This intentionally
        only recognises standard, non-secret NDEF content types.
        """
        clean=ANSI_RE.sub('',raw or '').replace('\x00','')
        fields={}; kind=None; content=''
        # vCard is easiest to recognise and preserves multiple useful fields.
        m=re.search(r'BEGIN:VCARD.*?END:VCARD',clean,re.I|re.S)
        if m:
            vc=m.group(0).replace('\\r','').replace('\\n','\n')
            def vf(name):
                x=re.search(r'(?im)^'+re.escape(name)+r'(?:;[^:]*)?:(.*)$',vc); return x.group(1).strip() if x else ''
            fields={'name':vf('FN'),'company':vf('ORG'),'phone':vf('TEL'),'email':vf('EMAIL'),'url':vf('URL')}
            kind='Contact / vCard'; content=vc
        if not kind:
            # Pull the first standard URI from decoded output. Avoid punctuation used by the renderer.
            u=re.search(r'(?i)\b(?:https?://|tel:|mailto:|sms:)[^\s\]\[<>"\']+',clean)
            if u:
                uri=u.group(0).rstrip('.,;)')
                content=uri
                if uri.lower().startswith(('http://','https://')): kind='Website / URL'; fields={'url':uri}
                elif uri.lower().startswith('tel:'): kind='Phone'; fields={'phone':uri[4:]}
                elif uri.lower().startswith('mailto:'):
                    kind='Email'; from urllib.parse import urlparse,parse_qs,unquote
                    q=urlparse(uri); qs=parse_qs(q.query); fields={'email':q.path,'subject':unquote(qs.get('subject',[''])[0]),'message':unquote(qs.get('body',[''])[0])}
                elif uri.lower().startswith('sms:'):
                    kind='SMS'; base,_,query=uri.partition('?'); from urllib.parse import parse_qs,unquote
                    qs=parse_qs(query); fields={'phone':base[4:],'message':unquote(qs.get('body',[''])[0])}
        if not kind:
            # RRG commonly prints a decoded text/payload line. Prefer it over the technical framing.
            for pat in (r'(?im)^\s*(?:text|payload|data)\s*[:=]\s*(.+)$',r'(?im)^\s*decoded\s*[:=]\s*(.+)$'):
                m=re.search(pat,clean)
                if m and m.group(1).strip(): kind='Plain Text'; content=m.group(1).strip(); fields={'message':content}; break
        if kind:
            labels={'Website / URL':'WEBSITE / URL','Contact / vCard':'CONTACT / VCARD','Phone':'PHONE','Email':'EMAIL','SMS':'SMS','Plain Text':'PLAIN TEXT'}
            display='NDEF CONTENT — '+labels.get(kind,kind.upper())+'\n'+content+'\n\n✓ NDEF record decoded successfully'
            return {'kind':kind,'fields':fields,'text':content,'display':display}
        # Do not send the user to Raw Console: show a cleaned, bounded version right here.
        useful='\n'.join(line for line in clean.splitlines() if line.strip())
        if len(useful)>1800: useful=useful[:1800]+'\n…'
        return {'kind':None,'fields':{},'text':useful,'display':'NDEF CONTENT — DECODED OUTPUT\n'+(useful or '(No NDEF content returned)')}

    def _close_nfc_studio(self,studio=None):
        win=studio or self.creator_window
        if win and win.winfo_exists():
            try: win.destroy()
            except Exception: pass
        self.creator_window=None
        try: self.home_body.pack(fill='both',expand=True)
        except Exception: pass

    # ---- v0.38 Workshop Centre -----------------------------------------------
    def _tag_profile(self,tag=None):
        tag=tag or self.last_tag or {}; kind=tag.get('kind','')
        profiles={
            'mfc':('HF 13.56 MHz','ISO14443-A','Sector/block memory','Key A / Key B authentication','Native dump + NDEF tools'),
            'mfu':('HF 13.56 MHz','ISO14443-A / NFC Type 2','Page memory','Password/authentication depends on model','Native dump + NDEF tools'),
            'mfp':('HF 13.56 MHz','ISO14443-A','MIFARE Plus memory','Security level / key authentication','Information + NDEF when supported'),
            'desfire':('HF 13.56 MHz','ISO14443-A / ISO14443-4','Application/file memory','Cryptographic application authentication','Read-only identification by default'),
            'iso15693':('HF 13.56 MHz','ISO15693 vicinity','Block memory','Security varies by manufacturer','Native dump supported'),
            'iclass':('HF 13.56 MHz','Picopass / ISO14443-B-derived','Page/block memory','Credential authentication may apply','Read-only identification by default'),
            'felica':('HF 13.56 MHz','FeliCa / ISO18092','Service/block based','Model/service dependent','Info + service/block inspection'),
            'iso14443b':('HF 13.56 MHz','ISO14443-B / NFC Type 4B','Model dependent','Application dependent','Info + dump + NDEF where present'),
            'ntag424':('HF 13.56 MHz','NTAG 424 DNA','File based','AES keys','Info + file inspection'),
            'seos':('HF 13.56 MHz','HID Seos','Application/file based','Cryptographic keys / SAM may apply','Info + authorised application reads'),
            'st25ta':('HF 13.56 MHz','ST25TA / NFC Type 4','NDEF/file based','Password/protection may apply','Info + NDEF'),
            'hitag':('LF 125 / 134 kHz','Hitag','Family-specific memory','Password/key may apply','Information tools'),
            'em410':('LF 125 kHz','EM410x','Read-only identifier','No authentication key','Identification only'),
            't55xx':('LF 125 kHz','T55xx','Configurable block memory','Password may be configured','Detect / inspect before writes'),
            'em4x05':('LF 125 kHz','EM4205 / EM4305 / EM4369 / EM4469','Word-addressed rewritable memory','Password may be configured','Info + dump + read/write + wipe'),
            'cotag':('LF 125 kHz','COTAG','Credential data','Family dependent','Read / identify'),
            'ioprox':('LF 125 kHz','Kantech ioProx','Credential identifier','No generic memory workflow','Read / identify'),
            'pyramid':('LF 125 kHz','Farpointe / Pyramid','Credential identifier','No generic memory workflow','Read / identify / simulate'),
            'viking':('LF 125 kHz','Viking','Credential identifier','No generic memory workflow','Read / identify / simulate'),
            'trovan':('LF 125 / 134 kHz','Trovan','Identifier','Family dependent','Read / identify'),
            'pcf7931':('LF 125 kHz','PCF7931','Writable transponder memory','Password/configuration may apply','Read / write'),
            'pac':('LF 125 kHz','PAC / Stanley','Credential identifier','No generic memory workflow','Read / identify / simulate'),
            'destron':('LF 125 / 134 kHz','FDX-A Destron','Animal-ID credential','No generic memory workflow','Read / identify'),
            'ti':('LF 134 kHz','Texas Instruments LF','R/W model dependent','Family dependent','Read / write where supported'),
            'gallagher':('LF 125 kHz','Gallagher','Credential identifier','Protocol dependent','Read / identify / simulate'),
            'idteck':('LF 125 kHz','IDTECK','Credential identifier','Protocol dependent','Read / identify / simulate'),
            'motorola':('LF 125 kHz','Motorola FlexPass','Credential identifier','Protocol dependent','Read / identify / simulate'),
            'nexwatch':('LF 125 kHz','NexWatch','Credential identifier','Protocol dependent','Read / identify / simulate'),
            'noralsy':('LF 125 kHz','Noralsy','Credential identifier','Protocol dependent','Read / identify / simulate'),
            'lto':('HF 13.56 MHz','LTO Cartridge Memory','Block memory','Device dependent','Info + dump + restore + block R/W'),
            'legic':('HF 13.56 MHz','LEGIC Prime','Writable tag memory','Family-specific access','Inspect / backup / erase where supported'),
            'emv':('HF 13.56 MHz','ISO14443-4 / EMV','Application data','Payment application security','Public card information only; sensitive data masked'),
            'type4a':('HF 13.56 MHz','ISO14443-4 / NFC Type 4','Application/file memory','Application dependent','NDEF when exposed by tag'),
            'hf14a':('HF 13.56 MHz','ISO14443-A','Unknown / family dependent','Unknown until identified','Information / technical analysis'),
            'unknown_lf':('LF 125 / 134 kHz','Unknown LF','Unknown','Unknown','Signal trace only'),
        }
        return profiles.get(kind,('Unknown','Unknown','Unknown','Unknown','Identify tag first'))

    def show_tag_dashboard(self):
        if not self.last_tag:
            messagebox.showinfo('Tag Dashboard','Scan a tag/transponder first.'); return
        tag=self.last_tag; freq,proto,memory,auth,tools=self._tag_profile(tag)
        w=self._tool_window('Tag Dashboard','860x610'); outer=ttk.Frame(w,style='Card.TFrame',padding=16); outer.pack(fill='both',expand=True,padx=10,pady=10)
        ttk.Label(outer,text='TAG DASHBOARD',style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(outer,text=tag.get('name','Unknown tag'),style='TagTitle.TLabel').pack(anchor='w',pady=(5,10))
        grid=ttk.Frame(outer,style='Card.TFrame'); grid.pack(fill='x')
        rows=[('Frequency',freq),('Protocol',proto),('UID / identifier',tag.get('uid') or 'Not reported'),('Memory',memory),('Authentication',auth),('Available workflow',tools),('NDEF','Available from NFC tools' if tag.get('kind') in ('mfc','mfu','mfp','type4a','iso14443b','st25ta','topaz') else 'Not currently exposed by Workshop')]
        for r,(a,b) in enumerate(rows):
            ttk.Label(grid,text=a.upper(),style='CardTitle.TLabel').grid(row=r,column=0,sticky='nw',padx=(0,18),pady=5); ttk.Label(grid,text=b,style='Card.TLabel',wraplength=600).grid(row=r,column=1,sticky='nw',pady=5)
        grid.columnconfigure(1,weight=1)
        ttk.Separator(outer).pack(fill='x',pady=12)
        ttk.Label(outer,text='WORKFLOW',style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(outer,text='Identify  →  Check access  →  Map / inspect memory  →  Backup  →  Work  →  Verify  →  Save',style='Card.TLabel',font=('Sans',11,'bold')).pack(anchor='w',pady=(5,10))
        bar=ttk.Frame(outer,style='Card.TFrame');bar.pack(fill='x')
        ttk.Button(bar,text='MEMORY / DUMP VIEWER',style='Action.TButton',command=self._context_callback(w,self.open_dump_viewer)).pack(side='left')
        ttk.Button(bar,text='BACKUP LIBRARY',style='Action.TButton',command=self._context_callback(w,self.show_backup_manager)).pack(side='left',padx=5)
        ttk.Button(bar,text='KEY DICTIONARIES',style='Action.TButton',command=self._context_callback(w,self.show_key_dictionary_manager)).pack(side='left')
        ttk.Button(bar,text='TECHNICAL CONSOLE',style='Action.TButton',command=lambda:(w.destroy(),self.set_console_mode(True))).pack(side='right')

    def show_authentication_tools(self):
        if not self.last_tag:
            messagebox.showinfo('Authentication / Key Check','Scan a card or tag first.'); return
        kind=self.last_tag.get('kind')
        w=self._tool_window('Authentication & Key Check — Proxmark3 Workshop','980x680')
        w.minsize(900,620); self.auth_window=w
        outer=ttk.Frame(w,style='Card.TFrame',padding=18); outer.pack(fill='both',expand=True,padx=12,pady=12)
        head=ttk.Frame(outer,style='Card.TFrame'); head.pack(fill='x')
        hleft=ttk.Frame(head,style='Card.TFrame'); hleft.pack(side='left',fill='x',expand=True)
        ttk.Label(hleft,text='AUTHENTICATION & KEY CHECK',style='TagTitle.TLabel',font=('Sans',17,'bold')).pack(anchor='w')
        ttk.Label(hleft,text='Check authorised credentials, map accessible sectors and review the result without leaving this tool.',style='Muted.TLabel').pack(anchor='w',pady=(2,0))
        ttk.Button(head,text='CLOSE',style='Action.TButton',command=w.destroy).pack(side='right',ipadx=14,ipady=5)

        card=ttk.Frame(outer,style='Card2.TFrame',padding=12); card.pack(fill='x',pady=(14,8))
        ttk.Label(card,text='CURRENT CARD',background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')).grid(row=0,column=0,columnspan=4,sticky='w')
        ttk.Label(card,text=self.last_tag.get('name','Unknown card'),background=self.PANEL2,foreground=self.TEXT,font=('Sans',15,'bold')).grid(row=1,column=0,columnspan=4,sticky='w',pady=(4,7))
        uid=self.last_tag.get('uid') or 'Not reported'
        supplied,custom=self._dictionary_files(); key_count=len(self._all_installed_keys())
        facts=[('UID / ID',uid),('TECHNOLOGY','MIFARE Classic' if kind=='mfc' else self.last_tag.get('name','Unknown')),('ACCESS','Not checked'),('KEY SOURCES',f'{len(supplied)} RRG • {len(custom)} My Keys')]
        fact_labels={}
        for i,(a,b) in enumerate(facts):
            box=ttk.Frame(card,style='Card2.TFrame'); box.grid(row=2,column=i,sticky='ew',padx=(0 if i==0 else 10,0))
            ttk.Label(box,text=a,background=self.PANEL2,foreground=self.MUTED,font=('Sans',8,'bold')).pack(anchor='w')
            lbl=ttk.Label(box,text=b,background=self.PANEL2,foreground=self.TEXT,font=('Sans',10,'bold')); lbl.pack(anchor='w',pady=(2,0)); fact_labels[a]=lbl
            card.columnconfigure(i,weight=1)

        work=ttk.Frame(outer,style='Card2.TFrame',padding=12); work.pack(fill='x',pady=(0,8))
        ttk.Label(work,text='AUTOMATIC KEY CHECK',background=self.PANEL2,foreground=self.TEXT,font=('Sans',12,'bold')).pack(anchor='w')
        ttk.Label(work,text=f'Uses the standard Proxmark3/RRG default-key check. Installed key library: {key_count:,} unique values. No card memory is written.',background=self.PANEL2,foreground=self.MUTED,wraplength=900).pack(anchor='w',pady=(3,8))
        prog_title=ttk.Label(work,text='READY',background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')); prog_title.pack(anchor='w')
        prog=ttk.Progressbar(work,mode='indeterminate',style='Horizontal.TProgressbar'); prog.pack(fill='x',ipady=3,pady=(4,3))
        prog_detail=ttk.Label(work,text='Press AUTO CHECK KEYS to begin.',background=self.PANEL2,foreground=self.MUTED); prog_detail.pack(anchor='w')

        result=ttk.Frame(outer,style='Card2.TFrame',padding=12); result.pack(fill='both',expand=True,pady=(0,8))
        ttk.Label(result,text='RESULTS',background=self.PANEL2,foreground='#79bdff',font=('Sans',9,'bold')).pack(anchor='w')
        result_title=ttk.Label(result,text='No key check has been run yet.',background=self.PANEL2,foreground=self.TEXT,font=('Sans',12,'bold')); result_title.pack(anchor='w',pady=(5,2))
        result_detail=ttk.Label(result,text='Accessible sectors, discovered keys and any warnings will be summarised here.',background=self.PANEL2,foreground=self.MUTED,wraplength=900,justify='left'); result_detail.pack(anchor='w')
        tech=tk.Text(result,height=7,bg=self.CONSOLE,fg='#c9def6',insertbackground='white',font=('Monospace',9),wrap='word',padx=9,pady=7)
        tech.pack(fill='both',expand=True,pady=(8,0)); tech.insert('1.0','Technical details will appear here after a check.'); tech.config(state='disabled')

        actions=ttk.Frame(outer,style='Card.TFrame'); actions.pack(fill='x')
        auto=ttk.Button(actions,text='AUTO CHECK KEYS',style='Primary.TButton'); auto.pack(side='left',fill='x',expand=True,ipady=5)
        ttk.Button(actions,text='ENTER KNOWN KEY',style='Action.TButton',command=lambda:messagebox.showinfo('Known Credential','Use Expert Command Centre for a specific authorised credential. Automatic guessing/recovery is not performed here.',parent=w)).pack(side='left',padx=7,ipady=5)
        ttk.Button(actions,text='KEY DICTIONARIES',style='Action.TButton',command=self._context_callback(w,self.show_key_dictionary_manager)).pack(side='left',ipady=5)

        state={'running':False}
        def start_check():
            if state['running']: return
            if kind!='mfc':
                result_title.config(text='Automatic default-key check is not available for this tag family.'); return
            if not self.connected:
                messagebox.showwarning(APP,'Connect to the Proxmark3 first.',parent=w); return
            state['running']=True; auto.config(state='disabled',text='CHECKING…'); prog.start(10)
            prog_title.config(text='CHECKING DEFAULT KEYS'); prog_detail.config(text='Proxmark3 is checking the standard/default MIFARE Classic keys…')
            result_title.config(text='Key check in progress…'); result_detail.config(text='Keep the card still on the HF antenna.')
            def worker():
                try:
                    out=self.pm.execute('hf mf chk'); self.q.put(('auth_done',(out,None)))
                except Exception as e:self.q.put(('auth_done',('',str(e))))
            threading.Thread(target=worker,daemon=True).start()
        auto.config(command=start_check)
        w._auth={'state':state,'auto':auto,'progress':prog,'progress_title':prog_title,'progress_detail':prog_detail,'result_title':result_title,'result_detail':result_detail,'tech':tech,'facts':fact_labels}
    def edit_tag_notes(self):
        if not self.last_tag:return
        key=(self.last_tag.get('kind','unknown')+'|'+(self.last_tag.get('uid') or self.last_tag.get('name','unknown'))).upper()
        w=self._tool_window('Job / Tag Notes','680x480'); outer=ttk.Frame(w,style='Card.TFrame',padding=14);outer.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(outer,text='JOB / TAG NOTES',style='CardTitle.TLabel').pack(anchor='w');ttk.Label(outer,text=self.last_tag.get('name',''),style='TagTitle.TLabel').pack(anchor='w',pady=(4,8))
        ttk.Label(outer,text='Keep workshop/reference notes here. Avoid storing unnecessary sensitive credentials.',style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        t=tk.Text(outer,bg=self.CONSOLE,fg='#c9def6',insertbackground='white',font=('Sans',10),wrap='word',padx=8,pady=8);t.pack(fill='both',expand=True);t.insert('1.0',self.tag_notes.get(key,''))
        def save(): self.tag_notes[key]=t.get('1.0','end-1c').strip();self._save_json(self.notes_path,self.tag_notes);messagebox.showinfo('Notes','Notes saved locally.');w.destroy()
        ttk.Button(outer,text='SAVE NOTES',style='Primary.TButton',command=save).pack(anchor='e',pady=(8,0))

    def show_command_centre(self):
        w=self._tool_window('Command Centre','980x650');outer=ttk.Frame(w,style='Card.TFrame',padding=12);outer.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(outer,text='COMMAND CENTRE',style='CardTitle.TLabel').pack(anchor='w');ttk.Label(outer,text='Search common RRG commands, read what they do, then insert or run them.',style='Muted.TLabel').pack(anchor='w',pady=(3,8))
        search=tk.StringVar();e=ttk.Entry(outer,textvariable=search);e.pack(fill='x',pady=(0,8))
        pane=ttk.Frame(outer,style='Card.TFrame');pane.pack(fill='both',expand=True);pane.columnconfigure(0,weight=1);pane.columnconfigure(1,weight=2);pane.rowconfigure(0,weight=1)
        tree=ttk.Treeview(pane,columns=('category','command'),show='headings');tree.heading('category',text='Category');tree.heading('command',text='Command');tree.column('category',width=140);tree.column('command',width=330);tree.grid(row=0,column=0,sticky='nsew',padx=(0,8))
        desc=tk.Text(pane,bg=self.CONSOLE,fg='#c9def6',font=('Sans',10),wrap='word',padx=10,pady=10);desc.grid(row=0,column=1,sticky='nsew')
        commands=[('Device','hw version','Show client, firmware and hardware/platform information.'),('Device','hw status','Show current hardware status.'),('Device','hw tune','Measure LF/HF antenna tuning and voltages.'),('HF','hf search','Identify a supported HF tag without writing.'),('HF','hf 14a info','Read ISO14443-A information.'),('MIFARE','hf mf info','Read MIFARE Classic information.'),('MIFARE','hf mf chk','Check standard/default Classic keys.'),('NFC','hf mfu ndefread','Read NDEF from Ultralight/NTAG.'),('NFC','hf mf ndefread','Read NDEF from MIFARE Classic.'),('ISO15693','hf 15 info','Read ISO15693 tag information.'),('LF','lf search','Identify a supported LF transponder.'),('Hitag','lf hitag info','Read Hitag information.'),('T55xx','lf t55xx detect','Detect T55xx configuration.'),('EMV','emv reader','Read public EMV application information; Workshop masks sensitive data in Clean Output.')]
        visible={}
        def fill(*_):
            tree.delete(*tree.get_children());visible.clear();q=search.get().lower().strip()
            for i,row in enumerate(commands):
                if q and q not in ' '.join(row).lower():continue
                iid=f'c{i}';visible[iid]=row;tree.insert('', 'end',iid=iid,values=(row[0],row[1]))
        def pick(_=None):
            sel=tree.selection();desc.delete('1.0','end')
            if sel:
                r=visible[sel[0]];desc.insert('end',r[1]+'\n'+'─'*44+'\n\n'+r[2]+'\n\nCommands that can change tag memory are intentionally not offered as one-click actions here.')
        tree.bind('<<TreeviewSelect>>',pick);search.trace_add('write',fill);fill()
        builder=ttk.Frame(outer,style='Card2.TFrame',padding=8);builder.pack(fill='x',pady=(8,0))
        ttk.Label(builder,text='COMMAND BUILDER',background=self.PANEL2,foreground='#79bdff',font=('Sans',8,'bold')).pack(side='left')
        opt=tk.StringVar();ttk.Entry(builder,textvariable=opt,width=42).pack(side='left',padx=8)
        ttk.Label(builder,text='Optional RRG arguments (for commands you understand)',background=self.PANEL2,foreground=self.MUTED).pack(side='left')
        bar=ttk.Frame(outer,style='Card.TFrame');bar.pack(fill='x',pady=(8,0))
        def selected():
            x=tree.selection();return visible[x[0]][1] if x else ''
        def insert():
            c=selected(); extra=opt.get().strip(); c=(c+' '+extra).strip() if c else ''
            if c:self.cmd.delete(0,'end');self.cmd.insert(0,c);w.destroy();self.cmd.focus_set()
        ttk.Button(bar,text='INSERT INTO MANUAL COMMAND',style='Action.TButton',command=insert).pack(side='left');ttk.Button(bar,text='RUN SELECTED',style='Primary.TButton',command=lambda:self._context_run(w,(selected()+' '+opt.get().strip()).strip()) if selected() else None).pack(side='right')

    def show_device_diagnostics(self):
        w=self._tool_window('Device Diagnostics','900x610');outer=ttk.Frame(w,style='Card.TFrame',padding=14);outer.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(outer,text='DEVICE DIAGNOSTICS',style='CardTitle.TLabel').pack(anchor='w');ttk.Label(outer,text='Read-only checks for USB, platform, firmware/client and antennas.',style='Muted.TLabel').pack(anchor='w',pady=(3,8))
        t=tk.Text(outer,bg=self.CONSOLE,fg='#c9def6',font=('Monospace',9),wrap='word',padx=10,pady=10);t.pack(fill='both',expand=True)
        def runchecks():
            if not self.connected:messagebox.showwarning('Diagnostics','Connect to the Proxmark3 first.');return
            t.delete('1.0','end');t.insert('end','● USB / client       connected\n');w.update_idletasks()
            for label,cmd in [('Hardware / platform','hw version'),('Hardware status','hw status'),('Antenna tuning','hw tune')]:
                try:
                    out=ANSI_RE.sub('',self.pm.execute(cmd)); ok=not any(x in out.lower() for x in ('error','failed','not found'))
                    t.insert('end',f"{'●' if ok else '▲'} {label:<20} {'OK' if ok else 'CHECK'}\n")
                    if label=='Hardware / platform':
                        for line in out.splitlines():
                            if any(k in line.lower() for k in ('platform name','client:','bootrom:','os:','flash')):t.insert('end','    '+line.strip()+'\n')
                    if label=='Antenna tuning':
                        for line in out.splitlines():
                            if any(k in line.lower() for k in ('125','134','13.56','antenna')):t.insert('end','    '+line.strip()+'\n')
                except Exception as ex:t.insert('end',f'▲ {label:<20} {ex}\n')
            t.insert('end','\nDiagnostics complete. Full raw output remains available through Technical Console when commands are run normally.\n')
        ttk.Button(outer,text='RUN DIAGNOSTICS',style='Primary.TButton',command=lambda:threading.Thread(target=runchecks,daemon=True).start()).pack(anchor='e',pady=(8,0))

    def show_offline_help(self):
        w=self._tool_window('Workshop Offline Help','900x620');outer=ttk.Frame(w,style='Card.TFrame',padding=12);outer.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(outer,text='OFFLINE HELP',style='CardTitle.TLabel').pack(anchor='w');ttk.Label(outer,text='Quick family and workflow reference — no internet required.',style='Muted.TLabel').pack(anchor='w',pady=(3,8))
        topics={'Workshop workflow':'Identify → understand/check access → map memory → backup → work → verify → save. Read-only checks are preferred before any write.', 'MIFARE Classic':'13.56 MHz sector/block memory. Access is controlled by Key A/Key B and access conditions. Back up readable data before authorised changes.', 'Ultralight / NTAG':'13.56 MHz page-based NFC tags. Many are used for NDEF. Capacity, lock bits and password support vary by model.', 'ISO15693':'13.56 MHz vicinity tags with block memory. Manufacturer/security features vary.', 'Hitag':'LF transponder family. Some variants use authentication. Use the correct family command and authorised credentials.', 'EM410x':'Simple LF identifier; normally no authentication key or writable user memory.', 'T55xx':'Configurable/re-writable LF family. Detect configuration before any authorised write.', 'Backups':'Workshop saves native dump files where supported and keeps metadata alongside them. A partial/failed read is never labelled as a complete backup.', 'Key dictionaries':'RRG dictionaries remain untouched. Imported lists are validated/deduplicated into My Keys. Backup My Keys creates one portable ZIP.'}
        left=ttk.Treeview(outer,show='tree',height=14);left.pack(side='left',fill='y',padx=(0,8));body=tk.Text(outer,bg=self.CONSOLE,fg='#c9def6',font=('Sans',10),wrap='word',padx=12,pady=12);body.pack(side='left',fill='both',expand=True)
        for i,k in enumerate(topics):left.insert('', 'end',iid=f'h{i}',text=k,values=(k,))
        def pick(_=None):
            sel=left.selection();body.delete('1.0','end')
            if sel:
                k=list(topics)[int(sel[0][1:])];body.insert('end',k.upper()+'\n'+'─'*50+'\n\n'+topics[k])
        left.bind('<<TreeviewSelect>>',pick);left.selection_set('h0');pick()

    def _load_json(self,path,default):
        try:
            with open(path,'r',encoding='utf-8') as f:return json.load(f)
        except Exception:return default
    def _save_json(self,path,obj):
        try:
            with open(path,'w',encoding='utf-8') as f:json.dump(obj,f,indent=2)
        except Exception:pass
    def _record_history(self,operation,tag,success):
        self.job_history.append({'time':datetime.datetime.now().isoformat(timespec='seconds'),'operation':operation,'tag':tag.get('name','Unknown') if tag else 'Unknown','uid':tag.get('uid','') if tag else '', 'success':bool(success)})
        self.job_history=self.job_history[-100:]; self._save_json(self.job_history_path,self.job_history)
    def show_history(self):
        w=tk.Toplevel(self); w.title('Recent Activity'); w.geometry('700x420'); t=tk.Text(w,font=('Monospace',10),padx=10,pady=10); t.pack(fill='both',expand=True)
        if not self.job_history:t.insert('end','No saved activity yet.')
        for x in reversed(self.job_history[-50:]):t.insert('end',f"{x.get('time','')}  {'✓' if x.get('success') else '✗'}  {x.get('operation','')}  {x.get('tag','')}  {x.get('uid','')}\n")
        t.config(state='disabled')
    def explain_current_tag(self):
        if not self.last_tag:return
        info={'mfc':('MIFARE Classic','13.56 MHz HF tag with sector-based memory and access keys. Common in legacy access, membership and ticketing systems.'),'mfp':('MIFARE Plus','13.56 MHz MIFARE family with stronger security modes; NFC/NDEF support depends on configuration.'),'type4a':('NFC Type 4A','ISO14443-4 NFC tag/application. NDEF operations depend on the tag exposing a compatible writable NFC application.'),'mfu':('Ultralight / NTAG','13.56 MHz NFC Type 2 family. Common for web links, product information, contact tags and other NDEF uses.'),'desfire':('MIFARE DESFire','13.56 MHz smart-card family with applications and cryptographic access control.'),'iso15693':('ISO15693','13.56 MHz vicinity-tag family designed for longer read range than proximity NFC tags.'),'iclass':('iCLASS / Picopass','13.56 MHz credential technology commonly used in access-control systems.'),'hitag':('Hitag','Low-frequency transponder family used in identification and some automotive applications.'),'em410':('EM410x','Simple 125 kHz read-only identifier commonly found in basic fobs and tags.'),'t55xx':('T55xx','Rewritable low-frequency transponder family with configurable memory.'),'felica':('FeliCa / ISO18092','13.56 MHz FeliCa family. Available services and authentication depend on the specific card.'),'iso14443b':('ISO14443-B','13.56 MHz Type-B contactless family. Memory and NDEF support depend on the specific tag.'),'ntag424':('NTAG 424 DNA','Secure NXP NFC tag with file-based access and AES authentication.'),'seos':('HID Seos','Cryptographic HF credential technology; available operations depend on authorised keys/SAM access.'),'st25ta':('ST25TA','ST NFC Type 4 tag family with NDEF and optional protection.'),'hf14a':('ISO14443-A','13.56 MHz contactless interface. A more specific card family may be identifiable with additional information.')}.get(self.last_tag['kind'],(self.last_tag['name'],'Detected contactless tag.'))
        messagebox.showinfo('Explain This Tag',f'{info[0]}\n\n{info[1]}\n\nDetected UID: {self.last_tag.get("uid") or "n/a"}')
    def stop_operation(self):
        if self.busy:
            if self.update_proc and self.update_proc.poll() is None:
                # During flashing we deliberately don't hard-kill from the main
                # Stop button; an interrupted flash can leave recovery work.
                if getattr(self,'update_flashing',False):
                    messagebox.showwarning('Update in progress','The Proxmark3 is currently flashing. Do not unplug it. Wait for flashing to finish or fail safely.'); return
                self.update_cancel=True
                try:self.update_proc.terminate()
                except Exception:pass
            self.pm.stop(); self.busy=False; self.finish_operation_progress('Operation stopped',False); self.stop_btn.config(state='disabled'); self.scan_btn.config(state='normal'); getattr(self,'scan_hf_btn',self.scan_btn).config(state='normal'); self.activity.config(text='Stopped'); self.status.config(text='●  CONNECTED' if self.connected else '●  DISCONNECTED')

    def _repo_dir(self):
        if self.pm.path:
            d=os.path.dirname(os.path.abspath(self.pm.path))
            if os.path.isdir(os.path.join(d,'.git')): return d
        d=os.path.expanduser('~/proxmark3')
        return d if os.path.isdir(os.path.join(d,'.git')) else None

    def _syscmd(self,args,cwd=None,timeout=1800):
        p=subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',timeout=timeout)
        return p.returncode,p.stdout

    def _stream_syscmd(self,args,cwd=None,timeout=1800,on_line=None):
        # Stream long build/flash commands so the GUI never looks frozen.
        import select
        p=subprocess.Popen(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',bufsize=1)
        self.update_proc=p; lines=[]; started=time.monotonic()
        try:
            while p.poll() is None:
                if self.update_cancel:
                    p.terminate(); return 130,''.join(lines)
                if time.monotonic()-started > timeout:
                    p.terminate(); return 124,''.join(lines)
                ready,_,_=select.select([p.stdout],[],[],0.25)
                if ready:
                    line=p.stdout.readline()
                    if line:
                        lines.append(line)
                        if on_line:on_line(line.rstrip('\n'))
            rest=p.stdout.read() or ''
            if rest:
                lines.append(rest)
                if on_line:
                    for line in rest.splitlines(): on_line(line)
            return p.returncode,''.join(lines)
        finally:
            self.update_proc=None


    # ---- v0.40 Smart Workspace / portability ---------------------------------
    def _workspace_support(self, kind):
        full={'mfc','mfu','mfp','iso15693','em410','t55xx','em4x05','hitag','type4a','legic','lto','felica','iso14443b','ntag424','seos','st25ta','topaz','cipurse','calypso','aliro','emrtd','fido','gst','ksx6924','saflok','secc','tesla','texkom','thinfilm','xerox','fudan','epa','fmcos','ict','jooki','vas','waveshare','hfgallagher','em4x50','em4x70','hitaghtu','visa2000'}
        standard={'desfire','iclass','emv','hf14a','unknown_lf'}
        return 'Full Workspace' if kind in full else ('Standard Workspace' if kind in standard else 'RRG / Technical Mode')

    def _family_actions(self, kind):
        # Read/inspect-first actions only. Family-specific destructive commands stay
        # behind the existing guarded tools and confirmations.
        reg=self._tag_registry().get(kind)
        if reg and reg.get('actions'):
            return list(reg['actions'])
        return {
          'mfc':[('Information','hf mf info'),('Check Default Keys','hf mf chk'),('Read NDEF','hf mf ndefread')],
          'mfu':[('Information','hf mfu info'),('Read NDEF','hf mfu ndefread')],
          'mfp':[('Information','hf mfp info'),('Check Keys','hf mfp chk'),('Dump / Backup','hf mfp dump'),('Read Block','hf mfp rdbl'),('Read Sector','hf mfp rdsc'),('Read NFC Data','nfc mf pread')],
          'type4a':[('ISO14443-A Info','hf 14a info'),('Read NDEF','hf 14a ndefread')],
          'desfire':[('Information','hf mfdes info'),('Get Version','hf mfdes getversion')],
          'iso15693':[('Information','hf 15 info'),('Reader','hf 15 reader'),('Dump / Backup','hf 15 dump'),('Read Block','hf 15 rdbl'),('Read Multiple Blocks','hf 15 rdmulti')],
          'legic':[('Information','hf legic info'),('Reader','hf legic reader')],
          'iclass':[('Read / Identify','hf iclass reader')],
          'hitag':[('Information','lf hitag info')],
          'em410':[('Read / Identify','lf em 410x reader')],
          't55xx':[('Detect','lf t55xx detect'),('Read Block 0','lf t55xx read -b 0')],
          'em4x05':[('Information','lf em 4x05 info'),('Dump / Backup','lf em 4x05 dump'),('Read Word','lf em 4x05 read')],
          'cotag':[('Read / Identify','lf cotag reader')],
          'ioprox':[('Read / Identify','lf io reader')],
          'pyramid':[('Read / Identify','lf pyramid reader')],
          'viking':[('Read / Identify','lf viking reader')],
          'trovan':[('Read / Identify','lf trovan reader')],
          'pcf7931':[('Read / Identify','lf pcf7931 reader')],
          'pac':[('Read / Identify','lf pac reader')],
          'destron':[('Read / Identify','lf destron reader')],
          'ti':[('Read / Identify','lf ti reader')],
          'gallagher':[('Read / Identify','lf gallagher reader')],
          'idteck':[('Read / Identify','lf idteck reader')],
          'motorola':[('Read / Identify','lf motorola reader')],
          'nexwatch':[('Read / Identify','lf nexwatch reader')],
          'noralsy':[('Read / Identify','lf noralsy reader')],
          'lto':[('Information','hf lto info'),('Dump / Backup','hf lto dump'),('Reader','hf lto reader')],
          'emv':[('Read Public EMV Info','emv reader'),('ISO14443-A Info','hf 14a info')],
          'hf14a':[('ISO14443-A Info','hf 14a info'),('HF Search','hf search')],
          'unknown_lf':[('LF Search','lf search'),('Antenna Tune','hw tune')],
        }.get(kind, [('HF Search','hf search'),('LF Search','lf search')])

    def show_smart_tag_workspace(self):
        if not self.last_tag:
            messagebox.showinfo('Smart Tag Workspace','Scan a tag/transponder first. Workshop will then select the appropriate family workspace.'); return
        tag=self.last_tag; kind=tag.get('kind','unknown'); freq,proto,memory,auth,tools=self._tag_profile(tag); support=self._workspace_support(kind)
        w=self._tool_window('Smart Tag Workspace — '+tag.get('name','Unknown'),'1000x680'); w._is_smart_workspace=True
        outer=ttk.Frame(w,style='Card.TFrame',padding=14); outer.pack(fill='both',expand=True,padx=8,pady=8)
        top=ttk.Frame(outer,style='Card.TFrame'); top.pack(fill='x')
        ttk.Label(top,text='SMART TAG WORKSPACE',style='CardTitle.TLabel').pack(anchor='w')
        smart_title=tk.StringVar(value=tag.get('name','Unknown'))
        ttk.Label(top,textvariable=smart_title,style='TagTitle.TLabel').pack(anchor='w',pady=(3,2))
        ttk.Label(top,text=f'{support}  •  {freq}  •  {proto}',style='Muted.TLabel').pack(anchor='w')
        body=ttk.Frame(outer,style='Card.TFrame'); body.pack(fill='both',expand=True,pady=(10,0)); body.columnconfigure(0,weight=3); body.columnconfigure(1,weight=2); body.rowconfigure(0,weight=1)
        info=ttk.Frame(body,style='Card2.TFrame',padding=12); info.grid(row=0,column=0,sticky='nsew',padx=(0,6))
        ttk.Label(info,text='TAG / TRANSPONDER',background=self.PANEL2,foreground=self.TEXT,font=('Sans',10,'bold')).pack(anchor='w')
        caps=self._tag_capabilities(tag)
        for a,b in [('UID / identifier',tag.get('uid') or 'Not reported'),('Memory',memory),('Authentication',auth),('Writable',caps['writable']),('Backup / dump',caps['backup']),('Erase / reset',caps['erase']),('Simulation',caps['simulate']),('NDEF',caps['ndef']),('Workspace support',support)]:
            ttk.Label(info,text=a.upper(),background=self.PANEL2,foreground='#79bdff',font=('Sans',8,'bold')).pack(anchor='w',pady=(8,0)); ttk.Label(info,text=b,background=self.PANEL2,foreground=self.TEXT,wraplength=540).pack(anchor='w')
        ttk.Label(info,text='WORKFLOW',background=self.PANEL2,foreground='#79bdff',font=('Sans',8,'bold')).pack(anchor='w',pady=(12,2)); ttk.Label(info,text='Identify → check access → inspect/map → backup → authorised work → verify → save',background=self.PANEL2,foreground=self.TEXT,wraplength=540).pack(anchor='w')
        detect_state=tk.StringVar(value='Ready — choose an action to inspect this tag further.')
        detail_state=tk.StringVar(value='')
        ttk.Label(info,text='DETECTION RESULT',background=self.PANEL2,foreground='#79bdff',font=('Sans',8,'bold')).pack(anchor='w',pady=(12,2))
        ttk.Label(info,textvariable=detect_state,background=self.PANEL2,foreground=self.TEXT,wraplength=540).pack(anchor='w')
        ttk.Label(info,textvariable=detail_state,background=self.PANEL2,foreground='#9fc9ef',wraplength=540).pack(anchor='w',pady=(3,0))
        w._smart_fields={'title':smart_title,'detect':detect_state,'detail':detail_state}
        actions=ttk.Frame(body,style='Card.TFrame'); actions.grid(row=0,column=1,sticky='nsew',padx=(6,0))
        ttk.Label(actions,text='RELEVANT ACTIONS',style='CardTitle.TLabel').pack(anchor='w',pady=(0,5))
        for label,cmd in self._family_actions(kind): ttk.Button(actions,text=label.upper(),style='Action.TButton',command=lambda c=cmd:self._smart_run(w,c)).pack(fill='x',pady=2)
        if kind in ('mfc','mfu','iso15693','iclass','legic'): ttk.Button(actions,text='BACKUP CURRENT TAG',style='Action.TButton',command=lambda:self._smart_child(w,self.backup_current_tag)).pack(fill='x',pady=2)
        if kind in ('mfc','mfu','mfp','hitag'): ttk.Button(actions,text='AUTHENTICATION / KEYS',style='Action.TButton',command=lambda:self._smart_child(w,self.show_authentication_tools)).pack(fill='x',pady=2)
        cap=self._erase_capability(tag)
        if cap: ttk.Button(actions,text=cap['label'],style='Action.TButton',command=lambda:self.erase_current_tag(w)).pack(fill='x',pady=2)
        ttk.Button(actions,text='VISUAL MEMORY MAP',style='Action.TButton',command=lambda:self._smart_child(w,self.show_live_memory_map)).pack(fill='x',pady=2)
        ttk.Button(actions,text='CAPTURE JOB / SESSION',style='Action.TButton',command=lambda:self._smart_child(w,self.capture_current_job)).pack(fill='x',pady=2)
        ttk.Button(actions,text='SAVE TO KNOWN TAG LIBRARY',style='Action.TButton',command=lambda:self._smart_child(w,self.save_current_tag_to_library)).pack(fill='x',pady=2)
        ttk.Button(actions,text='COMPARE TAG A / B',style='Action.TButton',command=lambda:self._smart_child(w,self.compare_tags)).pack(fill='x',pady=2)
        ttk.Button(actions,text='MEMORY / DUMP VIEWER',style='Action.TButton',command=lambda:self._smart_child(w,self.open_dump_viewer)).pack(fill='x',pady=2)
        ttk.Button(actions,text='COMMAND CENTRE',style='Action.TButton',command=lambda:self._smart_child(w,self.show_command_centre)).pack(fill='x',pady=2)
        ttk.Button(actions,text='JOB / TAG NOTES',style='Action.TButton',command=lambda:self._smart_child(w,self.edit_tag_notes)).pack(fill='x',pady=2)
        ttk.Button(actions,text='ALL FAMILY TOOLS',style='Primary.TButton',command=lambda:self._smart_child(w,self.show_card_tools)).pack(fill='x',pady=(7,2))
        ttk.Label(outer,text='If RRG recognises a family that Workshop does not yet richly parse, it remains available through RRG / Technical Mode and Command Centre instead of being misclassified.',style='Muted.TLabel',wraplength=940).pack(anchor='w',pady=(8,0))

    def _tag_key(self,tag=None):
        tag=tag or self.last_tag or {}
        return (str(tag.get('kind','unknown'))+'|'+str(tag.get('uid') or tag.get('name','unknown'))).upper()

    def capture_current_job(self):
        if not self.last_tag:
            messagebox.showinfo('Job Capture','Scan a tag/transponder first.'); return
        ref=simpledialog.askstring('Job Capture','Optional job/reference name:')
        entry={'time':datetime.datetime.now().isoformat(timespec='seconds'),'action':'Job capture','success':True,'reference':(ref or '').strip(),
               'tag':{k:self.last_tag.get(k) for k in ('name','kind','uid')},'notes':self.tag_notes.get(self._tag_key(),'')}
        self.job_history.append(entry);self.job_history=self.job_history[-1000:];self._save_json(self.job_history_path,self.job_history)
        messagebox.showinfo('Job Capture','Current tag summary and notes were saved to Workshop history. Credentials were not copied into the job summary.')

    def save_current_tag_to_library(self):
        if not self.last_tag:
            messagebox.showinfo('Known Tag Library','Scan a tag/transponder first.'); return
        key=self._tag_key(); old=self.tag_library.get(key,{})
        rec={'name':self.last_tag.get('name','Unknown'),'kind':self.last_tag.get('kind','unknown'),'uid':self.last_tag.get('uid') or '',
             'first_seen':old.get('first_seen') or datetime.datetime.now().isoformat(timespec='seconds'),
             'last_seen':datetime.datetime.now().isoformat(timespec='seconds'),'seen_count':int(old.get('seen_count',0))+1,
             'notes':self.tag_notes.get(key,'')}
        self.tag_library[key]=rec; self._save_json(self.tag_library_path,self.tag_library)
        messagebox.showinfo('Known Tag Library',f"Saved {rec['name']} to the local tag library.")

    def show_known_tag_library(self):
        w=self._tool_window('Known Tag Library','920x590'); f=ttk.Frame(w,style='Card.TFrame',padding=12);f.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(f,text='KNOWN TAG LIBRARY',style='CardTitle.TLabel').pack(anchor='w'); ttk.Label(f,text='Local reference of tags you deliberately save. Credentials are not stored here.',style='Muted.TLabel').pack(anchor='w',pady=(3,8))
        tree=ttk.Treeview(f,columns=('family','uid','seen','last'),show='headings');
        for c,t,wid in [('family','Family',180),('uid','UID / identifier',220),('seen','Seen',70),('last','Last seen',220)]:tree.heading(c,text=t);tree.column(c,width=wid)
        tree.pack(fill='both',expand=True)
        keys=[]
        for k,r in sorted(self.tag_library.items(),key=lambda kv:kv[1].get('last_seen',''),reverse=True):
            keys.append(k);tree.insert('','end',iid=str(len(keys)-1),values=(r.get('name'),r.get('uid') or 'n/a',r.get('seen_count',1),r.get('last_seen','')))
        bar=ttk.Frame(f,style='Card.TFrame');bar.pack(fill='x',pady=(8,0))
        ttk.Button(bar,text='SAVE CURRENT TAG',style='Primary.TButton',command=self._context_callback(w,self.save_current_tag_to_library)).pack(side='left')
        def forget():
            if not tree.selection():return
            k=keys[int(tree.selection()[0])]
            if messagebox.askyesno('Known Tag Library','Remove this reference entry? This does not delete tag backups.'):
                self.tag_library.pop(k,None);self._save_json(self.tag_library_path,self.tag_library);tree.delete(tree.selection()[0])
        ttk.Button(bar,text='REMOVE REFERENCE',style='Action.TButton',command=forget).pack(side='right')

    def compare_tags(self):
        if not self.last_tag:
            messagebox.showinfo('Compare Tags','Scan Tag A first.'); return
        if self.compare_tag_a is None:
            self.compare_tag_a=dict(self.last_tag); messagebox.showinfo('Compare Tags','Tag A captured.\n\nNow scan Tag B, then choose Compare Tag A / B again.'); return
        a=self.compare_tag_a; b=dict(self.last_tag); self.compare_tag_a=None
        w=self._tool_window('Compare Tag A / B','760x500');f=ttk.Frame(w,style='Card.TFrame',padding=14);f.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(f,text='TAG A / TAG B COMPARISON',style='CardTitle.TLabel').pack(anchor='w')
        t=tk.Text(f,bg=self.CONSOLE,fg='#c9def6',font=('Monospace',10),wrap='word',padx=10,pady=10);t.pack(fill='both',expand=True,pady=(8,0))
        for field,label in [('name','Technology'),('kind','Family'),('uid','UID / identifier')]:
            av=str(a.get(field) or 'n/a');bv=str(b.get(field) or 'n/a');t.insert('end',f'{label:<18} A: {av}\n{"":<18} B: {bv}\n{"":<18} {"SAME" if av==bv else "DIFFERENT"}\n\n')
        t.insert('end','For byte-level memory differences, create authorised backups of both tags and use Compare Two Dumps.\n');t.config(state='disabled')

    def _backup_health(self,path):
        try:
            size=os.path.getsize(path); meta={}
            if os.path.isfile(path+'.json'):
                try: meta=self._load_json(path+'.json',{})
                except Exception: pass
            if size<=0:return 'Read errors'
            expected=meta.get('expected_size') or meta.get('size')
            if expected:
                try:
                    if size<int(expected):return 'Partial'
                    if size==int(expected):return 'Complete'
                except Exception:pass
            if size in (64,128,256,512,1024,2048,4096,8192):return 'Unverified'
            return 'Unverified'
        except Exception:return 'Read errors'

    def show_backup_health(self):
        w=self._tool_window('Backup Health','900x570');f=ttk.Frame(w,style='Card.TFrame',padding=12);f.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(f,text='BACKUP HEALTH',style='CardTitle.TLabel').pack(anchor='w');ttk.Label(f,text='Workshop never calls an unknown-size dump complete unless metadata verifies the expected size.',style='Muted.TLabel').pack(anchor='w',pady=(3,8))
        tree=ttk.Treeview(f,columns=('health','size','date'),show='tree headings');tree.heading('#0',text='Backup');tree.heading('health',text='Health');tree.heading('size',text='Bytes');tree.heading('date',text='Modified');tree.pack(fill='both',expand=True)
        counts={}
        for path in sorted(glob.glob(os.path.join(self.backup_dir,'**','*'),recursive=True)):
            if not os.path.isfile(path) or path.endswith('.json') or path.endswith('.zip'):continue
            h=self._backup_health(path);counts[h]=counts.get(h,0)+1;st=os.stat(path);tree.insert('','end',text=os.path.basename(path),values=(h,st.st_size,datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')))
        ttk.Label(f,text='  •  '.join(f'{k}: {v}' for k,v in counts.items()) or 'No backups found.',style='Card.TLabel').pack(anchor='w',pady=(8,0))

    def show_live_memory_map(self):
        path=filedialog.askopenfilename(title='Select dump for visual memory map',initialdir=self.backup_dir,filetypes=[('Dump files','*.bin *.mfd'),('All files','*.*')])
        if not path:return
        try:data=self._read_dump_bytes(path)
        except Exception as e:messagebox.showerror('Memory Map',str(e));return
        w=self._tool_window('Live Memory Map — '+os.path.basename(path),'980x650');f=ttk.Frame(w,style='Card.TFrame',padding=12);f.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(f,text='VISUAL MEMORY MAP',style='CardTitle.TLabel').pack(anchor='w');ttk.Label(f,text='Select a block to inspect its hex/ASCII. Classic sector trailers are identified by position; protected/unreadable state requires authenticated read metadata.',style='Muted.TLabel',wraplength=920).pack(anchor='w',pady=(3,8))
        canvas=tk.Canvas(f,bg=self.CONSOLE,highlightthickness=0);canvas.pack(fill='both',expand=True)
        detail=tk.StringVar(value='Select a block.');ttk.Label(f,textvariable=detail,style='Card.TLabel').pack(anchor='w',pady=(8,0))
        blocks=[data[i:i+16] for i in range(0,len(data),16)]; cols=16; cellw=54;cellh=32
        for i,ch in enumerate(blocks):
            r,c=divmod(i,cols);x=8+c*cellw;y=8+r*cellh; trailer=(len(data) in (1024,4096) and ((i<128 and i%4==3) or (i>=128 and (i-128)%16==15)))
            fill='#503d18' if trailer else '#0d2b47'; tag=f'b{i}';canvas.create_rectangle(x,y,x+48,y+25,fill=fill,outline='#1d5b8d',tags=(tag,));canvas.create_text(x+24,y+13,text=str(i),fill='#e9f2ff',tags=(tag,))
            def click(ev,idx=i,chunk=ch,tr=trailer):
                hx=' '.join(f'{v:02X}' for v in chunk);asc=''.join(chr(v) if 32<=v<127 else '.' for v in chunk);detail.set(f'Block {idx}  •  {"Sector trailer / access configuration" if tr else "Data block"}  •  {hx}  |{asc}|')
            canvas.tag_bind(tag,'<Button-1>',click)
        canvas.configure(scrollregion=(0,0,cols*cellw+20,((len(blocks)+cols-1)//cols)*cellh+20))

    def export_diagnostic_report(self):
        path=filedialog.asksaveasfilename(title='Save Diagnostic Report',defaultextension='.txt',initialfile=f'proxmark3-workshop-diagnostics-{datetime.datetime.now().strftime("%Y%m%d-%H%M%S")}.txt',filetypes=[('Text report','*.txt')])
        if not path:return
        lines=[f'{APP} v{VERSION}',f'Generated: {datetime.datetime.now().isoformat(timespec="seconds")}',f'Connected: {self.connected}',f'Active backend: {self.active_backend}',f'PM3 client: {self.pm.path}',f'Port: {self.pm.port}',f'Hardware profile: {self.settings.get("hardware_profile","not set")}',f'Build target: {self.settings.get("firmware_platform","PM3GENERIC")} + {self.settings.get("firmware_extras","")}',f'Last command: {self.current_cmd}','', 'Recent technical output:', self.raw_log[-12000:]]
        with open(path,'w',encoding='utf-8') as fh:fh.write('\n'.join(lines))
        messagebox.showinfo('Diagnostic Report',f'Report saved.\n\n{path}')

    def toggle_privacy_mode(self):
        cur=bool(self.settings.get('privacy_mode',True));self.settings['privacy_mode']=not cur;self._save_json(self.settings_path,self.settings);messagebox.showinfo('Privacy Mode',f'Privacy Mode is now {"ON" if not cur else "OFF"}.\n\nWhen ON, Workshop clean views should mask payment-card identifiers and avoid displaying Track 2 data.')

    def _portable_paths(self):
        return [('config',self.config_dir),('data',self.data_dir),('backups',self.backup_dir)]

    def export_portable_workshop(self):
        stamp=datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')
        path=filedialog.asksaveasfilename(title='Export Portable Workshop Backup',defaultextension='.zip',initialfile=f'Proxmark3-Workshop-Portable-{stamp}.zip',filetypes=[('Workshop backup','*.zip')])
        if not path:return
        include_dumps=messagebox.askyesno('Portable Backup','Include Proxmark dump/backup files too?\n\nChoose No for a smaller settings/keys/notes/templates backup.')
        manifest={'format':'Proxmark3 Workshop Portable Backup','version':1,'workshop_version':VERSION,'created':datetime.datetime.now().isoformat(timespec='seconds'),'includes_dumps':include_dumps}
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json',json.dumps(manifest,indent=2))
            for label,root in self._portable_paths():
                if label=='backups' and not include_dumps:continue
                if not os.path.isdir(root):continue
                for base,dirs,files in os.walk(root):
                    for fn in files:
                        src=os.path.join(base,fn); rel=os.path.relpath(src,root); z.write(src,os.path.join(label,rel))
        messagebox.showinfo('Portable Backup',f'Backup created successfully.\n\n{path}')

    def restore_portable_workshop(self):
        path=filedialog.askopenfilename(title='Restore Portable Workshop Backup',filetypes=[('Workshop backup','*.zip')])
        if not path:return
        try:
            with zipfile.ZipFile(path,'r') as z:
                names=z.namelist()
                if 'manifest.json' not in names: raise ValueError('Missing manifest.json')
                m=json.loads(z.read('manifest.json').decode('utf-8'))
                if m.get('format')!='Proxmark3 Workshop Portable Backup': raise ValueError('Not a Workshop portable backup')
                if not messagebox.askyesno('Restore Portable Backup','Merge this Workshop backup into the current installation?\n\nExisting files are preserved where possible. Settings files from the backup will replace matching settings.'):return
                roots={'config':self.config_dir,'data':self.data_dir,'backups':self.backup_dir}
                for name in names:
                    if name.endswith('/') or name=='manifest.json':continue
                    parts=name.split('/',1)
                    if len(parts)!=2 or parts[0] not in roots:continue
                    rel=os.path.normpath(parts[1])
                    if rel.startswith('..') or os.path.isabs(rel):continue
                    dst=os.path.join(roots[parts[0]],rel); os.makedirs(os.path.dirname(dst),exist_ok=True)
                    with z.open(name) as r, open(dst,'wb') as f: shutil.copyfileobj(r,f)
            messagebox.showinfo('Restore Complete','Portable Workshop data restored. Restart Workshop so all restored settings are reloaded.')
        except Exception as e: messagebox.showerror('Restore Failed',str(e))

    def show_portable_backup(self):
        w=self._tool_window('Portable Workshop Backup','650x360'); f=ttk.Frame(w,style='Card.TFrame',padding=18); f.pack(fill='both',expand=True,padx=10,pady=10)
        ttk.Label(f,text='PORTABLE WORKSHOP BACKUP',style='CardTitle.TLabel').pack(anchor='w'); ttk.Label(f,text='Move or protect your Workshop setup with one ZIP. It can include My Keys, settings, templates, notes, history and optionally native tag dump files.',style='Muted.TLabel',wraplength=590).pack(anchor='w',pady=(5,16))
        ttk.Button(f,text='EXPORT WORKSHOP BACKUP',style='Primary.TButton',command=self.export_portable_workshop).pack(fill='x',pady=5); ttk.Button(f,text='RESTORE WORKSHOP BACKUP',style='Action.TButton',command=self.restore_portable_workshop).pack(fill='x',pady=5); ttk.Button(f,text='BACKUP MY KEYS ONLY',style='Action.TButton',command=self.backup_my_keys).pack(fill='x',pady=5)

    def show_gui_rollback(self):
        root=os.path.expanduser('~/.local/share/proxmark3-workshop-gui-rollback')
        backups=sorted([x for x in glob.glob(os.path.join(root,'*')) if os.path.isdir(os.path.join(x,'app'))],reverse=True)
        if not backups: messagebox.showinfo('Restore Previous GUI','No GUI rollback backups were found yet. They are created automatically by the GUI-only updater.'); return
        w=self._tool_window('Restore Previous Workshop GUI','720x430'); f=ttk.Frame(w,style='Card.TFrame',padding=14);f.pack(fill='both',expand=True,padx=8,pady=8)
        ttk.Label(f,text='RESTORE PREVIOUS GUI',style='CardTitle.TLabel').pack(anchor='w'); ttk.Label(f,text='This restores application files only. My Keys, settings, notes, history and tag backups are not rolled back.',style='Muted.TLabel',wraplength=660).pack(anchor='w',pady=(4,10))
        lb=tk.Listbox(f,bg=self.CONSOLE,fg=self.TEXT,selectbackground=self.ACCENT,font=('Monospace',10));lb.pack(fill='both',expand=True)
        for x in backups:lb.insert('end',os.path.basename(x))
        def restore():
            if not lb.curselection():return
            src=backups[lb.curselection()[0]]; appdir=os.path.expanduser('~/.local/share/proxmark3-workshop-gui')
            if not messagebox.askyesno('Restore GUI','Restore the selected previous GUI? Workshop will close after the files are restored.'):return
            tmp=appdir+'.rollback'; shutil.rmtree(tmp,ignore_errors=True); shutil.copytree(os.path.join(src,'app'),tmp); shutil.rmtree(appdir,ignore_errors=True); os.replace(tmp,appdir)
            messagebox.showinfo('Restore GUI','Previous GUI restored. Re-open Workshop.'); self.destroy()
        ttk.Button(f,text='RESTORE SELECTED VERSION',style='Primary.TButton',command=restore).pack(anchor='e',pady=(8,0))

    def _rrg_remote_revision(self,repo):
        """Return (branch, revision) for the repository's real origin default branch.
        Works with master/main and detached local checkouts.
        """
        rc,ref=self._syscmd(['git','symbolic-ref','--quiet','--short','refs/remotes/origin/HEAD'],repo,20)
        if not rc and ref.strip().startswith('origin/'):
            branch=ref.strip().split('/',1)[1]
            rc,rev=self._syscmd(['git','rev-parse',ref.strip()],repo,20)
            if not rc and rev.strip(): return branch,rev.strip()
        rc,branch=self._syscmd(['git','branch','--show-current'],repo,20); branch=branch.strip() if not rc else ''
        for candidate in [branch,'master','main']:
            if not candidate: continue
            rc,rev=self._syscmd(['git','rev-parse',f'origin/{candidate}'],repo,20)
            if not rc and rev.strip(): return candidate,rev.strip()
        return '', ''

    def open_updater(self):
        if self.busy:
            messagebox.showwarning('Updates','Finish or stop the current Proxmark operation first.'); return
        repo=self._repo_dir()
        if not repo:
            messagebox.showerror('Updates','Could not find the RRG Proxmark3 Git checkout. Expected ~/proxmark3.'); return
        w=tk.Toplevel(self); w.title('Proxmark3 Software & Firmware Updates'); w.geometry('720x500'); w.minsize(650,440)
        f=ttk.Frame(w,padding=18); f.pack(fill='both',expand=True)
        ttk.Label(f,text='PROXMARK3 SOFTWARE & FIRMWARE',font=('Sans',15,'bold')).pack(anchor='w')
        ttk.Label(f,text='Check the official RRG repository first. When Update is pressed this window closes and progress moves to the main console.',foreground=self.MUTED,wraplength=670).pack(anchor='w',pady=(4,12))
        status=tk.StringVar(value='Ready to check for updates.')
        ttk.Label(f,textvariable=status,font=('Sans',11,'bold')).pack(anchor='w',pady=(0,8))
        info=tk.Text(f,height=11,font=('Monospace',9),wrap='word',padx=8,pady=8); info.pack(fill='both',expand=True)
        info.insert('end',f'Repository: {repo}\n'); info.config(state='disabled')
        prog=ttk.Progressbar(f,mode='indeterminate'); prog.pack(fill='x',pady=(10,8))
        startup=tk.BooleanVar(value=bool(self.settings.get('check_updates_startup',True)))
        ttk.Checkbutton(f,text='Check for updates when the app starts',variable=startup).pack(anchor='w')
        btns=ttk.Frame(f); btns.pack(fill='x',pady=(12,0))
        update_btn=ttk.Button(btns,text='UPDATE PROXMARK3',style='Primary.TButton',state='disabled'); update_btn.pack(side='right')
        check_btn=ttk.Button(btns,text='CHECK FOR UPDATES'); check_btn.pack(side='right',padx=(0,8))
        reflash_btn=ttk.Button(btns,text='REPAIR / REFLASH CURRENT BUILD'); reflash_btn.pack(side='left')
        state={'remote':None,'head':None,'dirty':False,'branch':None}
        def popup_log(msg):
            def put():
                if not w.winfo_exists():return
                info.config(state='normal'); info.insert('end',msg.rstrip()+'\n'); info.see('end'); info.config(state='disabled')
            self.after(0,put)
        def save_startup(*_):
            self.settings['check_updates_startup']=startup.get(); self._save_json(self.settings_path,self.settings)
        startup.trace_add('write',save_startup)
        def setbusy(on,msg=None):
            if not w.winfo_exists():return
            check_btn.config(state='disabled' if on else 'normal'); reflash_btn.config(state='disabled' if on else 'normal')
            if on:update_btn.config(state='disabled');prog.start(12)
            else:prog.stop()
            if msg:status.set(msg)
        def check_worker():
            self.after(0,lambda:setbusy(True,'Checking official RRG repository…'))
            rc,out=self._syscmd(['git','fetch','--prune','origin'],repo,120); popup_log(out)
            if rc:self.after(0,lambda:setbusy(False,'Update check failed — see details.'));return
            _,head=self._syscmd(['git','rev-parse','HEAD'],repo,20); head=head.strip()
            branch,remote=self._rrg_remote_revision(repo)
            _,desc=self._syscmd(['git','describe','--always','--tags','--dirty'],repo,20)
            _,rdesc=self._syscmd(['git','describe','--always','--tags',remote],repo,20) if remote else (1,'unknown')
            _,dirtyout=self._syscmd(['git','status','--porcelain'],repo,20);dirty=bool(dirtyout.strip())
            state.update(head=head,remote=remote,dirty=dirty,branch=branch)
            popup_log(f'Installed: {desc.strip()}\nLatest:    {rdesc.strip()}\nLocal source changes: '+('YES — will be backed up' if dirty else 'No'))
            def done():
                setbusy(False,'✓ Update available' if remote and head!=remote else '✓ Already up to date')
                if remote and head!=remote:update_btn.config(state='normal')
            self.after(0,done)
        def check_now():threading.Thread(target=check_worker,daemon=True).start()

        def launch_main_update(reflash=False):
            if not messagebox.askyesno('Update Proxmark3',('Rebuild and reflash the CURRENT RRG source?' if reflash else 'Update to the checked RRG revision, build matching client/firmware and flash the device?')+'\n\nProgress will stay fixed above the technical output. Keep the Proxmark3 connected once flashing starts.\n\nContinue?'):return
            w.destroy(); self.deiconify(); self.lift(); self.focus_force()
            self.update_cancel=False; self.update_flashing=False; self.busy=True
            self.scan_btn.config(state='disabled'); self.create_btn.config(state='disabled'); self.connect_btn.config(state='disabled'); self.stop_btn.config(state='normal')
            self.set_console_mode(False); self.console.delete('1.0','end')
            self.console_title.config(text='UPDATE TECHNICAL OUTPUT')
            # Keep this dashboard fixed. Only self.console below it scrolls.
            self.update_panel.pack(fill='x',before=self.console)
            stages=['Check repository','Backup local changes','Download source changes','Clean previous build','Build client + firmware','Start flasher','Flash device firmware','Reconnect device','Verify versions','Complete']
            ust={'idx':0,'states':['○ Waiting']*len(stages),'started':time.monotonic(),'last':time.monotonic(),'headline':'Preparing update…','safe':True,'success':False}
            def render():
                if not self.winfo_exists():return
                elapsed=int(time.monotonic()-ust['started']); mins,secs=divmod(elapsed,60)
                done=sum(1 for x in ust['states'] if x.startswith('✓'))
                running=any(x.startswith('▶') for x in ust['states'])
                pct=min(100,int(((done + (0.35 if running else 0))/len(stages))*100))
                if ust['success']: pct=100
                idle=int(time.monotonic()-ust['last'])
                self.update_overall['value']=pct; self.update_percent.config(text=f'{pct}%')
                self.update_headline.config(text=ust['headline'])
                self.update_safety.config(text=('SAFE TO DISCONNECT — device is not being flashed' if ust['safe'] else '⚠ DO NOT UNPLUG THE PROXMARK3 — FLASHING IN PROGRESS'))
                self.update_timing.config(text=f'Elapsed {mins:02d}:{secs:02d}   •   Last activity {idle}s ago')
                for i,(name,state_txt) in enumerate(zip(stages,ust['states'])):
                    self.update_stage_labels[i].config(text=f'{i+1}. {name}   {state_txt}')
                self.activity.config(text=f'Update {pct}% • {ust["headline"][:34]}')
            def tick():
                if self.busy and getattr(self,'update_mode',False):render();self.after(1000,tick)
            def stage(i,headline,safe=True):
                for j in range(i):
                    if not ust['states'][j].startswith('✗'):ust['states'][j]='✓ Complete'
                ust['states'][i]='▶ Working…';ust['idx']=i;ust['headline']=headline;ust['safe']=safe;ust['last']=time.monotonic();self.update_flashing=not safe
                self.after(0,lambda:(self.update_stage_progress.start(12),render()))
            def finish_stage(i):
                ust['states'][i]='✓ Complete';ust['last']=time.monotonic();self.after(0,render)
            def live(line):
                if not line.strip(): return
                clean=ANSI_RE.sub('',line)
                ust['last']=time.monotonic()
                low=clean.lower()
                # Give useful flashing sub-status without pretending compiler output is percentage-complete.
                if ust['idx']==6:
                    if 'bootrom' in low or 'bootloader' in low: ust['headline']='Flashing bootloader…'
                    elif 'fullimage' in low or 'full image' in low: ust['headline']='Flashing full firmware image…'
                    elif 'waiting for proxmark' in low: ust['headline']='Waiting for Proxmark3 bootloader…'
                def append():
                    self.console.insert('end',clean if clean.endswith('\n') else clean+'\n');self.console.see('end');render()
                self.after(0,append)
            def fail(i,msg):
                ust['states'][i]='✗ Failed';ust['headline']=msg;ust['safe']=not self.update_flashing
                self.after(0,lambda:(self.update_stage_progress.stop(),render(),messagebox.showerror('Proxmark3 Update Failed',msg+'\n\nSee UPDATE TECHNICAL OUTPUT for details.')))
            self.update_mode=True;self.after(1000,tick);render()
            def worker():
                stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');backup=os.path.join(os.path.expanduser('~/.cache/proxmark3-workshop/firmware-updates'),stamp);os.makedirs(backup,exist_ok=True);old=state.get('head') or ''
                try:
                    stage(0,'Checking repository…');finish_stage(0)
                    stage(1,'Saving local changes…')
                    if not reflash and state.get('dirty'):
                        self._syscmd(['git','diff','HEAD','--binary','--output',os.path.join(backup,'local-changes.patch')],repo,30)
                        rc,out=self._stream_syscmd(['git','stash','push','-u','-m',f'Proxmark3 Workshop automatic backup {stamp}'],repo,120,live)
                        if rc:raise RuntimeError('Could not safely back up local source changes.')
                    finish_stage(1)
                    stage(2,'Downloading/applying source changes…')
                    if not reflash:
                        target=state.get('remote')
                        if not target:raise RuntimeError('No checked remote revision. Run Check for Updates again.')
                        rc,_=self._stream_syscmd(['git','checkout',state.get('branch') or 'master'],repo,60,live)
                        if rc:raise RuntimeError('Could not select update branch.')
                        rc,_=self._stream_syscmd(['git','merge','--ff-only',target],repo,120,live)
                        if rc:raise RuntimeError('Could not fast-forward source tree.')
                    finish_stage(2)
                    # Use the hardware target selected by the Linux installer/settings.
                    # Preserve the previous platform file before changing it.
                    platform=self.settings.get('firmware_platform','PM3GENERIC').strip() or 'PM3GENERIC'
                    extras=self.settings.get('firmware_extras','').strip()
                    allowed={'PM3RDV4','PM3GENERIC','PM3ICOPYX','PM3ULTIMATE'}
                    if platform not in allowed:
                        raise RuntimeError('Unknown saved firmware target: '+platform)
                    platform_file=os.path.join(repo,'Makefile.platform')
                    if os.path.exists(platform_file):
                        try:
                            shutil.copy2(platform_file,os.path.join(backup,'Makefile.platform.before-update'))
                        except Exception:
                            pass
                    with open(platform_file,'w',encoding='utf-8') as pf:
                        pf.write('PLATFORM='+platform+'\n')
                        if extras: pf.write('PLATFORM_EXTRAS='+extras+'\n')
                    target_desc=platform + ((' + '+extras) if extras else '')
                    live('[GUI] Firmware target: '+target_desc+'\n')
                    stage(3,'Cleaning previous '+platform+' build…');rc,_=self._stream_syscmd(['make','clean'],repo,300,live)
                    if rc:raise RuntimeError('Clean failed. Nothing has been flashed.')
                    finish_stage(3)
                    stage(4,'Building matching client + '+target_desc+' firmware…');jobs=str(max(1,os.cpu_count() or 2));rc,_=self._stream_syscmd(['make','-j'+jobs],repo,1800,live)
                    if rc:raise RuntimeError('Build failed. Nothing has been flashed.')
                    finish_stage(4)
                    flasher=os.path.join(repo,'pm3-flash-all')
                    if not os.path.isfile(flasher):raise RuntimeError('pm3-flash-all was not produced by the build.')
                    stage(5,'Starting official flasher…',True);finish_stage(5)
                    stage(6,'Waiting for Proxmark3 flasher output…',False)
                    rc,_=self._stream_syscmd([flasher],repo,600,live)
                    self.update_flashing=False;ust['safe']=True
                    if rc:raise RuntimeError('Flashing did not complete successfully. --force was NOT used.')
                    ust['headline']='Firmware flashing complete ✓';finish_stage(6)
                    stage(7,'Waiting for Proxmark3 to reconnect…');time.sleep(2);finish_stage(7)
                    stage(8,'Verifying client and device versions…');rc,verify_out=self._stream_syscmd([os.path.join(repo,'pm3'),'-c','hw version'],repo,120,live)
                    if rc:raise RuntimeError('Flash completed but verification failed.')
                    finish_stage(8)
                    # Update scratch/rollback files are useful only until the new
                    # client + firmware have been flashed and verified.  After a
                    # confirmed success remove all old firmware-update work files
                    # and recreate the empty parent directory for the next run.
                    update_root=os.path.expanduser('~/.cache/proxmark3-workshop/firmware-updates')
                    try:
                        if os.path.isdir(update_root): shutil.rmtree(update_root)
                        os.makedirs(update_root,exist_ok=True)
                        live('[GUI] Update cleanup complete — temporary firmware/update files removed.\n')
                    except Exception as cleanup_err:
                        live('[GUI] Warning: update succeeded but temporary-file cleanup failed: '+str(cleanup_err)+'\n')
                    stage(9,'UPDATE COMPLETE — firmware flashed, verified and temporary files cleaned ✓');finish_stage(9)
                    ust['success']=True;ust['safe']=True
                    self.after(0,lambda:(self.update_stage_progress.stop(),render(),self.show_update_complete_dialog(verify_out)))
                except Exception as e:
                    fail(ust['idx'],'UPDATE STOPPED: '+str(e))
                finally:
                    self.update_flashing=False
                    if not ust.get('success'):
                        self.update_mode=False;self.busy=False
                        def restore():
                            self.scan_btn.config(state='normal');getattr(self,'scan_hf_btn',self.scan_btn).config(state='normal');self.create_btn.config(state='normal');self.connect_btn.config(state='normal');self.stop_btn.config(state='disabled');self.status.config(text='●  CONNECTED' if self.connected else '●  DISCONNECTED');render()
                        self.after(0,restore)
            threading.Thread(target=worker,daemon=True).start()
        update_btn.config(command=lambda:launch_main_update(False));reflash_btn.config(command=lambda:launch_main_update(True));check_btn.config(command=check_now)
        check_now()

    def show_update_complete_dialog(self,verify_out=''):
        # Custom dialog avoids theme/window-manager issues seen with the tiny
        # blank native messagebox and lets OK restore the normal workspace.
        w=tk.Toplevel(self); w.title('Proxmark3 Update Complete'); w.transient(self); w.grab_set(); w.resizable(False,False)
        f=ttk.Frame(w,padding=22); f.pack(fill='both',expand=True)
        ttk.Label(f,text='✓  UPDATE COMPLETE',font=('Sans',16,'bold')).pack(anchor='w')
        ttk.Label(f,text='Firmware flashing and verification completed successfully.',wraplength=480).pack(anchor='w',pady=(6,14))
        ttk.Label(f,text='✓ Client updated / rebuilt\n✓ Firmware flashed\n✓ Device reconnected\n✓ Client/device version check completed',justify='left').pack(anchor='w')
        def done():
            try:w.grab_release();w.destroy()
            except Exception:pass
            self.finish_update_ui()
        ttk.Button(f,text='OK — RETURN TO WORKSHOP',style='Primary.TButton',command=done).pack(anchor='e',pady=(18,0))
        w.protocol('WM_DELETE_WINDOW',done); self.update_idletasks()
        try:
            x=self.winfo_rootx()+(self.winfo_width()-w.winfo_reqwidth())//2; y=self.winfo_rooty()+(self.winfo_height()-w.winfo_reqheight())//2; w.geometry(f'+{max(0,x)}+{max(0,y)}')
        except Exception:pass
    def finish_update_ui(self):
        self.update_mode=False; self.update_flashing=False; self.busy=False; self.update_cancel=False
        try:self.update_stage_progress.stop(); self.update_panel.pack_forget()
        except Exception:pass
        self.scan_btn.config(state='normal'); getattr(self,'scan_hf_btn',self.scan_btn).config(state='normal'); self.create_btn.config(state='normal'); self.connect_btn.config(state='normal'); self.stop_btn.config(state='disabled')
        self.activity.config(text='✓ Firmware update complete')
        self.clear_console(); self.scanner_home(); self.refresh_ports()
        # Flashing re-enumerates USB. Reconnect afresh rather than trusting the
        # pre-flash connection flag.
        self.connected=False; self.connect_btn.config(text='CONNECT'); self.status.config(text='●  RECONNECTING…'); self.device.config(text='Waiting for Proxmark3…')
        p=self.port.get(); p=None if p in ('','Auto detect','Auto detect supported devices') else p
        threading.Thread(target=self._connect,args=(p,),daemon=True).start()
        self._record_history('Firmware update',None,True)

    def copy_diagnostics(self):
        text=f'{APP} v{VERSION}\nConnected: {self.connected}\nPM3: {self.pm.path}\nPort: {self.pm.port}\nDevice: {self.device.cget("text")}\nLast command: {self.current_cmd}\nLast activity: {self.activity.cget("text")}\n'
        self.clipboard_clear(); self.clipboard_append(text); messagebox.showinfo(APP,'Diagnostics copied to clipboard.')
    def open_settings(self):
        w=tk.Toplevel(self); w.title('Settings / Hardware Profile'); w.geometry('620x390'); f=ttk.Frame(w,padding=18); f.pack(fill='both',expand=True)
        ttk.Label(f,text='Workshop Library',style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(f,text=self.library_root,style='Muted.TLabel').pack(anchor='w',pady=(2,6))
        librow=ttk.Frame(f); librow.pack(fill='x',pady=(0,10))
        ttk.Button(librow,text='OPEN LIBRARY',style='Action.TButton',command=lambda:subprocess.Popen(['xdg-open',self.library_root])).pack(side='left')
        ttk.Button(librow,text='MANAGE LINKED BACKUP FOLDERS',style='Action.TButton',command=self.manage_linked_folders).pack(side='left',padx=7)
        raw=tk.BooleanVar(value=self.raw_mode); ttk.Checkbutton(f,text='Start with raw console visible',variable=raw).pack(anchor='w',pady=(0,10))
        ttk.Separator(f).pack(fill='x',pady=6);ttk.Label(f,text='PROXMARK HARDWARE PROFILE',style='CardTitle.TLabel').pack(anchor='w',pady=(4,4))
        ttk.Label(f,text='The saved profile controls future firmware builds. Changing it never flashes the device automatically.',style='Muted.TLabel',wraplength=560).pack(anchor='w',pady=(0,7))
        profiles={'Proxmark3 Easy / Generic + external flash':('PM3GENERIC','FLASH'),'Proxmark3 Generic (no extras)':('PM3GENERIC',''),'Proxmark3 RDV4':('PM3RDV4',''),'iCopy-X':('PM3ICOPYX',''),'Proxmark3 Ultimate':('PM3ULTIMATE','')}
        current=(self.settings.get('firmware_platform','PM3GENERIC'),self.settings.get('firmware_extras','FLASH')); selected=next((n for n,x in profiles.items() if x==current),'Proxmark3 Easy / Generic + external flash')
        pv=tk.StringVar(value=selected);combo=ttk.Combobox(f,textvariable=pv,state='readonly',values=list(profiles),width=48);combo.pack(anchor='w',pady=(0,7))
        profile_detail=ttk.Label(f,text='',style='Muted.TLabel');profile_detail.pack(anchor='w')
        def detail(*_):
            a,b=profiles[pv.get()];profile_detail.config(text=f'Makefile.platform: PLATFORM={a}'+(f'   PLATFORM_EXTRAS={b}' if b else ''))
        pv.trace_add('write',detail);detail()
        def save():
            platform,extras=profiles[pv.get()]; self.settings.update({'backup_dir':self.library_root,'dump_library_chosen':True,'linked_backup_folders':self.linked_backup_folders,'raw_console':raw.get(),'remember_window':True,'firmware_platform':platform,'firmware_extras':extras,'hardware_profile':pv.get(),'privacy_mode':bool(self.settings.get('privacy_mode',True))}); self._save_json(self.settings_path,self.settings); w.destroy()
        ttk.Button(f,text='SAVE SETTINGS',style='Primary.TButton',command=save).pack(anchor='e',pady=16)
    def _startup_update_check(self):
        if not self.settings.get('check_updates_startup',True): return
        repo=self._repo_dir()
        if not repo:return
        def worker():
            self.after(0,lambda:self.footer_status.config(text='●  Checking RRG updates…'))
            rc,_=self._syscmd(['git','fetch','--quiet','--prune','origin'],repo,90)
            if rc:
                self.after(0,lambda:self.footer_status.config(text='●  RRG update check unavailable — use Firmware / Update to retry'))
                return
            _,head=self._syscmd(['git','rev-parse','HEAD'],repo,20); head=head.strip()
            branch,remote=self._rrg_remote_revision(repo)
            if head!=remote and remote and self.settings.get('dismissed_update_revision','')!=remote:
                _,local_desc=self._syscmd(['git','describe','--always','--tags',head],repo,20)
                _,remote_desc=self._syscmd(['git','describe','--always','--tags',remote],repo,20)
                def notify():
                    open_it=messagebox.askyesno('New Proxmark3 Update Found',f'A newer RRG Proxmark3 version is available.\n\nInstalled: {local_desc.strip() or head[:10]}\nAvailable: {remote_desc.strip() or remote[:10]}\n\nOpen the updater now?')
                    if open_it:self.open_updater()
                    else:
                        self.settings['dismissed_update_revision']=remote;self._save_json(self.settings_path,self.settings)
                self.after(0,notify)
            else:
                self.after(0,lambda:self.footer_status.config(text='●  Proxmark3 selected — RRG update check complete'))
        threading.Thread(target=worker,daemon=True).start()

    def _device_watch(self):
        try:
            ports=self.pm.ports()
            active_port=self.pm.port
            if self.connected and active_port and active_port not in ports and active_port.startswith('/dev/'):
                self.connected=False; self.status.config(text='●  DISCONNECTED'); self.activity.config(text='Device unplugged'); self.connect_btn.config(text='CONNECT',state='normal'); self._set_device_art('generic',False)
            if ports!=self._last_ports:
                self._last_ports=ports; self.refresh_ports()
        finally:self.after(1500,self._device_watch)

    def confirm_run(self, title, detail, cmd):
        if messagebox.askyesno(title,detail): self.run(cmd)

    def open_help(self,kind):
        cmd={'mfc':'hf mf help','mfu':'hf mfu help','mfp':'hf mfp help','desfire':'hf mfdes help','iso15693':'hf 15 help','iclass':'hf iclass help','hitag':'lf hitag help','em410':'lf em 410x help','t55xx':'lf t55xx help','felica':'hf felica help','iso14443b':'hf 14b help','ntag424':'hf ntag424 help','seos':'hf seos help','st25ta':'hf st25ta help','hf14a':'hf 14a help'}.get(kind)
        if not cmd and kind in self._tag_registry():
            info=self._tag_registry()[kind].get('info','')
            parts=info.split(); cmd=(' '.join(parts[:2])+' help') if len(parts)>=2 else 'help'
        self.run(cmd or 'help')
    def friendly_operation(self,cmd):
        names={'auto':'Scanning tag','hf search':'Scanning HF','lf search':'Scanning LF','hw version':'Reading device information','hw status':'Reading hardware status','hw tune':'Checking antennas','emv reader':'Reading EMV payment card','hf mf info':'Reading card information','lf t55xx read -b 0':'Reading T55xx block','hf mfu ndefformat':'Formatting NFC tag','hf mf ndefformat':'Formatting NFC tag','hf mfp ndefformat':'Formatting NFC tag','hf 14a ndefformat':'Formatting NFC tag','hf mfu ndefread':'Verifying NFC content','hf mf ndefread':'Verifying NFC content','hf mfp ndefread':'Verifying NFC content','hf 14a ndefread':'Verifying NFC content'}
        if cmd.startswith('hf mf dump'):return 'Saving MIFARE Classic backup'
        if cmd.startswith('hf mfu dump'):return 'Saving Ultralight / NTAG backup'
        if cmd.startswith('hf 15 dump'):return 'Saving ISO15693 backup'
        if cmd.startswith('hf iclass dump'):return 'Saving iCLASS backup'
        if cmd.startswith('hf mf restore'):return 'Restoring MIFARE Classic backup'
        return names.get(cmd,cmd)
    def parse_mfc_info(self,out):
        clean=ANSI_RE.sub('',out)
        info={}
        pats={
            'UID':r'\bUID:\s*([^\r\n]+)',
            'ATQA':r'\bATQA:\s*([^\r\n]+)',
            'SAK':r'\bSAK:\s*([^\r\n]+)',
            'Block 0':r'Block 0\.{2,}\s*([^\r\n]+)'
        }
        for k,pat in pats.items():
            m=re.search(pat,clean,re.I)
            if m: info[k]=m.group(1).strip()
        m=re.search(r'--- Fingerprint.*?\n\[\+\]\s*([^\r\n]+)',clean,re.I|re.S)
        if m: info['Fingerprint']=m.group(1).strip()
        if re.search(r'Static nonce.*?yes',clean,re.I|re.S): info['Nonce']='Static'
        keys=[]
        for sec,side,key in re.findall(r'Sector\s+(\d+)\s+key\s+([AB])\.{2,}\s*([0-9A-Fa-f]{12})',clean,re.I):
            keys.append((sec,side,key.upper()))
        info['Keys']=keys
        return info
    def parse_emv_info(self,out):
        """Extract friendly EMV fields without retaining sensitive Track 2 data."""
        clean=ANSI_RE.sub('',out or '')
        info={}
        # Strip PM3 status prefixes so labels are easier to match.
        lines=[]
        for raw in clean.splitlines():
            line=re.sub(r'^\s*\[[+!\-=#]\]\s*','',raw).strip()
            if line: lines.append(line)
        text='\n'.join(lines)

        def grab(labels):
            for label in labels:
                # RRG commonly uses either ':' or a run of dots between label/value.
                pat=r'(?im)^\s*'+label+r'\s*(?:\.{2,}|:|=)\s*([^\r\n]+?)\s*$'
                m=re.search(pat,text)
                if m:
                    v=m.group(1).strip(' .')
                    if v:return v
            return ''

        info['UID']=grab([r'UID'])
        info['AID']=grab([r'AID',r'Application Identifier'])
        info['Application']=grab([r'Application Label',r'Application Preferred Name',r'Application'])
        info['Issuer']=grab([r'Issuer Name',r'Issuer'])
        info['Cardholder']=grab([r'Cardholder Name'])
        info['Expiry']=grab([r'Expiration Date',r'Expiry Date',r'Expires'])
        info['Effective']=grab([r'Effective Date'])
        info['Country']=grab([r'Country Code',r'Issuer Country Code'])
        info['Currency']=grab([r'Currency Code',r'Transaction Currency Code'])
        info['ATC']=grab([r'Application Transaction Counter',r'ATC'])
        info['PIN tries']=grab([r'PIN Try Counter',r'PIN tries'])
        pan=grab([r'PAN',r'Primary Account Number'])
        digits=re.sub(r'\D','',pan)
        if len(digits)>=4:
            info['PAN']='•••• •••• •••• '+digits[-4:]

        low=text.lower()
        aid=re.sub(r'[^0-9A-Fa-f]','',info.get('AID','')).upper()
        network='EMV Payment Card'
        if 'mastercard' in low or 'master card' in low or 'maestro' in low or aid.startswith('A000000004'):
            network='Mastercard'
        elif re.search(r'\bvisa\b',low) or aid.startswith('A000000003'):
            network='Visa'
        elif 'american express' in low or 'amex' in low or aid.startswith('A000000025'):
            network='American Express'
        info['Network']=network
        return info

    def show_clean_emv(self,out):
        info=self.parse_emv_info(out)
        # Update the detected-tag model and left summary with the useful safe fields.
        issuer=info.get('Issuer','').strip()
        network=info.get('Network','EMV Payment Card')
        title=(issuer+' • '+network) if issuer else network+' / EMV Payment Card' if network!='EMV Payment Card' else network
        uid=info.get('UID','') or (self.last_tag.get('uid','') if self.last_tag else '')
        if self.last_tag:
            self.last_tag.update({'kind':'emv','name':title,'uid':uid,'extra':'Payment data masked'})
        else:
            self.last_tag={'kind':'emv','name':title,'uid':uid,'extra':'Payment data masked'}
        self.tag_title.config(text=title)
        self.tag_badge.config(text='● Payment card detected',foreground=self.GREEN)
        rows=['HF • 13.56 MHz']
        if uid: rows.append('UID      '+uid)
        if info.get('Application'): rows.append('App      '+info['Application'])
        if info.get('AID'): rows.append('AID      '+info['AID'])
        rows.append('PAN      '+info.get('PAN','[not displayed]'))
        self.tag_meta.config(text='\n'.join(rows))

        self.clear_console()
        self.console.insert('end',title+'\n'+'─'*52+'\n\n')
        ordered=[('UID','UID'),('Network','Payment network'),('Application','Application'),('Issuer','Issuer'),
                 ('AID','AID'),('PAN','Card number'),('Cardholder','Cardholder'),('Expiry','Expiry'),
                 ('Effective','Effective date'),('Country','Country code'),('Currency','Currency code'),
                 ('ATC','Transaction counter'),('PIN tries','PIN try counter')]
        shown=0
        for key,label in ordered:
            val=info.get(key,'')
            if val:
                self.console.insert('end',f'{label:<20} {val}\n'); shown+=1
        self.console.insert('end','\nTrack 2             [hidden in Clean Output]\n')
        if shown<=2:
            self.console.insert('end','\nOnly limited public card information was returned by this card.\n')
        self.console.insert('end','\n✓ EMV card information complete. Sensitive payment data is masked.\n')

    def _intelligent_clean_summary(self,cmd,out):
        """Translate common RFID command output into concise user-facing meaning."""
        clean=ANSI_RE.sub('',out or ''); low=clean.lower(); lines=[]
        def val(label):
            m=re.search(r'(?im)^\s*(?:\[[^]]+\]\s*)?'+label+r'\s*(?:[:=]|\.{2,})\s*([^\r\n]+)',clean)
            return m.group(1).strip() if m else ''
        if 'ndef' in cmd.lower():
            d=self._decode_ndef_output(clean)
            if d.get('kind'):
                return f"✓ NFC CONTENT READ SUCCESSFULLY\n\nType: {d['kind']}\n\n{d.get('text','')}\n\nThe tag contains a standard NFC/NDEF record."
        if cmd.startswith('hf mf dump'):
            m=re.search(r'(?i)(\d+)\s+bytes',clean); size=m.group(1) if m else ''
            return '✓ MIFARE CLASSIC BACKUP COMPLETE\n\n'+(('Dump size: '+size+' bytes\n') if size else '')+'The readable card memory was saved. Keep the dump as the original backup before making changes.'
        if cmd.startswith('hf mfu dump'):
            return '✓ NTAG / ULTRALIGHT BACKUP COMPLETE\n\nThe tag pages were read and saved. Protection and lock settings are preserved where the reader reported them.'
        if cmd.startswith('lf em 410x') or ('em410' in low and cmd.startswith('lf')):
            ident=val(r'(?:ID|Card ID|EM TAG ID)')
            return '✓ EM410X CREDENTIAL DETECTED\n\nFrequency: 125 kHz'+(('\nCard ID: '+ident) if ident else '')+'\n\nThis family normally carries a fixed identifier rather than general user memory.'
        if cmd in ('hf search','lf search','auto') and self.last_tag:
            tag=self.last_tag; freq='LF 125 / 134 kHz' if tag.get('kind') in ('hitag','em410','t55xx','unknown_lf') else 'HF 13.56 MHz'
            text=f"✓ CARD / TAG IDENTIFIED\n\nType: {tag.get('name','Unknown')}\nFrequency: {freq}"
            if tag.get('uid'): text+=f"\nUID / ID: {tag['uid']}"
            text+='\n\nWorkshop has selected the tools appropriate for this technology.'
            return text
        return ''

    def show_clean_result(self,cmd,out):
        if cmd=='emv reader':
            self.show_clean_emv(out)
            return
        smart=self._intelligent_clean_summary(cmd,out)
        if smart:
            self.clear_console(); self.console.insert('end',smart+'\n'); return
        self.clear_console()
        if self.last_tag:self.console.insert('end',self.last_tag['name']+'\n'+'─'*44+'\n\n')
        if cmd=='hf mf info':
            info=self.parse_mfc_info(out)
            for k in ('UID','ATQA','SAK','Fingerprint','Nonce','Block 0'):
                if info.get(k): self.console.insert('end',f'{k}: {info[k]}\n')
            if info.get('Keys'):
                self.console.insert('end','\nKnown keys found:\n')
                for sec,side,key in info['Keys']:
                    self.console.insert('end',f'  Sector {sec} Key {side}: {key}  ✓\n')
            self.console.insert('end','\n✓ Card information complete.\n')
            # Enrich the tag summary without cluttering it.
            if self.last_tag:
                extras=[]
                if info.get('Fingerprint'): extras.append(info['Fingerprint'])
                if info.get('Nonce'): extras.append(info['Nonce']+' nonce')
                if extras:
                    base=('LF • 125 / 134 kHz' if self.last_tag['kind'] in ('hitag','em410','t55xx','em4x05','hidprox','awid','indala','fdxb','nedap','paradox','presco','securakey','jablotron','keri','gproxii','cotag','ioprox','pyramid','viking','trovan','pcf7931','pac','destron','ti','gallagher','idteck','motorola','nexwatch','noralsy') else 'HF • 13.56 MHz')
                    if self.last_tag.get('uid'): base+='\nUID   '+self.last_tag['uid']
                    base+='\n'+' • '.join(extras)
                    self.tag_meta.config(text=base)
            return
        clean=ANSI_RE.sub('',out)
        # Payment-card privacy: normal/clean view never exposes a full PAN or Track 2.
        # The underlying RRG output is retained only in Technical Console for authorised diagnostics.
        if cmd.startswith('emv '):
            clean=re.sub(r'(?im)^(\s*(?:PAN)\.{0,20}[: ]+)([0-9][0-9 ]{8,}[0-9])\s*$',
                         lambda m:m.group(1)+'•••• •••• •••• '+re.sub(r'\D','',m.group(2))[-4:], clean)
            clean=re.sub(r'(?im)^\s*Track\s*2[^\r\n]*$', 'Track 2 equivalent..... [hidden in normal view]', clean)
        useful=[]
        for line in clean.splitlines():
            t=line.strip()
            if not t or t.startswith('[GUI]') or 'Session log' in t or 'execute command' in t or 'Using UART port' in t or 'Communicating with PM3' in t or 'Max frame size' in t or 'Emulator memory' in t or 'loaded `' in t: continue
            useful.append(line)
        self.console.insert('end','\n'.join(useful[-120:]).strip() or '✓ Operation complete.')
        self.console.insert('end','\n\n✓ Operation complete.\n')
    def explain_failure(self,cmd,raw,err):
        text=ANSI_RE.sub('',raw).lower()
        if cmd=='hf mf dump':
            return ('Backup could not be completed', 'MIFARE Classic sectors normally need valid authorised keys before a complete dump can be read. Read Card Information first, then use keys you are authorised to use. The raw PM3 output is available with Show Raw Console.')
        if 'no tag' in text or 'no card' in text or 'timeout' in text:
            return ('Tag not detected', 'Keep the tag on the correct LF/HF antenna and try the operation again.')
        if 'auth' in text or 'authentication' in text:
            return ('Authentication required', 'This area is protected. Use authorised credentials for this tag, then retry.')
        return ('Operation failed', str(err))
    def show_clean_failure(self,title,detail):
        self.clear_console()
        if self.last_tag:self.console.insert('end',self.last_tag['name']+'\n'+'─'*44+'\n\n')
        self.console.insert('end','✗ '+title+'\n\n'+detail+'\n')
    def clear_console(self):
        self.console.delete('1.0','end')
    def show_clean_tag_header(self,tag):
        if self.raw_mode:return
        self.clear_console(); self.console.insert('end',tag['name']+'\n'+'─'*44+'\n')
        if tag.get('uid'):self.console.insert('end','UID: '+tag['uid']+'\n')
        self.console.insert('end','\nReady.\n')
    def show_clean_operation(self,name):
        self.clear_console();
        if self.last_tag:self.console.insert('end',self.last_tag['name']+'\n'+'─'*44+'\n\n')
        self.console.insert('end',name+'…\n')
    def set_console_mode(self,raw):
        self.raw_mode=raw; self.clear_console()
        self.console_title.config(style='Tab.TButton' if raw else 'ActiveTab.TButton'); self.console_mode_btn.config(style='ActiveTab.TButton' if raw else 'Tab.TButton')
        if raw:self.console.insert('end',ANSI_RE.sub('',self.raw_log));self.console.see('end')
        elif self.last_tag:self.show_clean_tag_header(self.last_tag)
        else:self.console.insert('end','Ready.\n')
    def toggle_console_mode(self):self.set_console_mode(not self.raw_mode)
    def history_up(self,event=None):
        if self.history:
            self.history_pos=max(0,self.history_pos-1); self.cmd.delete(0,'end'); self.cmd.insert(0,self.history[self.history_pos])
        return 'break'
    def history_down(self,event=None):
        if self.history:
            self.history_pos=min(len(self.history),self.history_pos+1); self.cmd.delete(0,'end')
            if self.history_pos<len(self.history):self.cmd.insert(0,self.history[self.history_pos])
        return 'break'
    def send_manual(self):
        c=self.cmd.get().strip()
        if c:
            self.history.append(c); self.history=self.history[-50:]; self.history_pos=len(self.history); self.cmd.delete(0,'end'); self.run(c)
    def save_log(self):
        p=filedialog.asksaveasfilename(defaultextension='.txt',filetypes=[('Text log','*.txt'),('All files','*.*')],initialfile='proxmark3-log.txt')
        if p:
            with open(p,'w',encoding='utf-8') as f:f.write(self.console.get('1.0','end-1c'))
    def _drain(self):
        try:
            while True:
                typ,val=self.q.get_nowait()
                if typ=='log':
                    self.raw_log+=val; self._touch_operation_progress(val)
                    if self.raw_mode:self.console.insert('end',ANSI_RE.sub('',val));self.console.see('end')
                elif typ=='connected':self.connected=True;self.busy=False;self.active_backend='pm3';self.connect_btn.config(text='DISCONNECT',state='normal');self.status.config(text='●  CONNECTED');self.activity.config(text='Ready');self._update_device_card(val or '');self._show_backend_ui('pm3')
                elif typ=='command_result':
                    cmd,out=val
                    backup_result=None
                    if getattr(self,'pending_backup',None) and ((' dump ' in (' '+cmd+' ')) or cmd.startswith('hf mf autopwn')): backup_result=self._finish_backup(cmd,out)
                    if not self.raw_mode:
                        if backup_result is False:
                            self.clear_console(); self.console.insert('end','✗ CARD READ FINISHED — LIBRARY BACKUP NOT SAVED\n\nOpen Technical Details to see the PM3 output.\n')
                        else:self.show_clean_result(cmd,out)
                elif typ=='idle':self.busy=False;self.status.config(text='●  CONNECTED');self.activity.config(text='✓ '+self.current_op+' complete');self.scan_btn.config(state='normal');getattr(self,'scan_hf_btn',self.scan_btn).config(state='normal');self.stop_btn.config(state='disabled');self.finish_operation_progress(self.current_op+' complete',True);self._activity_feedback(self.current_op,self.current_op+' complete.',False,True);self._record_history(self.current_op,self.last_tag,True)
                elif typ=='restore_target_result':
                    self._finish_restore_target_check(val)
                elif typ=='restore_write_stage':
                    rw,st,dt=val
                    try:
                        if rw.winfo_exists(): rw._restore_status.set(st); rw._restore_detail.set(dt)
                    except Exception: pass
                elif typ=='restore_write_result':
                    self._finish_restore_write(val)
                elif typ=='t55xx_program_result':
                    self._finish_t55xx_program(val)
                elif typ=='scan_result':
                    tag=self.refine_tag_identity(self.parse_tag(val),val)
                    tag=self._canonical_tag_kind(tag)
                    if tag and tag.get('kind')=='t55xx':
                        fmt,cid=self._extract_t55xx_encoded_credential(val)
                        tag['encoded_format']=fmt; tag['credential_id']=cid
                    self.show_tag_menu(tag)
                    # If the scan itself returned EMV application data, render the full
                    # friendly card summary immediately instead of leaving Clean Output at UID only.
                    if tag and tag.get('kind')=='emv' and not self.raw_mode:
                        self.show_clean_emv(val)
                    self.activity.config(text=('✓ '+tag['name']+' detected') if tag else 'Scan complete')
                elif typ=='details':self.device.config(text=val)
                elif typ=='creator_status':
                    if self.creator_window and self.creator_window.winfo_exists(): self.creator_window._creator_status.config(text=val)
                elif typ=='creator_progress':
                    if self.creator_window and self.creator_window.winfo_exists():
                        pct,title,detail=val; c=self.creator_window._creator; c['progress']['value']=pct; c['progress_title'].config(text=title); c['progress_stage'].config(text=detail); c.get('update_batch_popup',lambda *a:None)(pct,title,detail)
                elif typ=='creator_profile':
                    if self.creator_window and self.creator_window.winfo_exists():
                        profile=val; c=self.creator_window._creator; c['target'].update(profile); c['targetbox'].config(text=f"TARGET: {profile['name']}  •  UID {profile.get('uid') or 'n/a'}"); c['update_preview']()
                elif typ=='creator_result':
                    msg,profile=val
                    if self.creator_window and self.creator_window.winfo_exists():
                        c=self.creator_window._creator; c['check'].config(state='normal'); c['status'].config(text=msg)
                        if profile:
                            c['target'].update(profile); c['targetbox'].config(text=f"TARGET: {profile['name']}  •  UID {profile.get('uid') or 'n/a'}")
                            c['format'].config(state='normal'); c['write'].config(state='normal'); c['erase'].config(state='normal'); c['reset'].config(state='normal' if profile.get('kind')=='mfc' else 'disabled'); c['read'].config(state='normal'); c['verify'].config(state='normal'); c['duplicate'].config(state='normal')
                            c['backup'].config(state='normal' if profile.get('kind') in ('mfc','mfu') else 'disabled'); c['restore'].config(state='normal' if profile.get('kind') in ('mfc','mfu') else 'disabled'); c['lock'].config(state='disabled'); c['update_preview']()
                        else:
                            c['format'].config(state='disabled'); c['write'].config(state='disabled'); c['erase'].config(state='disabled'); c['reset'].config(state='disabled'); c['read'].config(state='disabled'); c['verify'].config(state='disabled'); c['duplicate'].config(state='disabled'); c['backup'].config(state='disabled'); c['restore'].config(state='disabled'); c['lock'].config(state='disabled')
                elif typ=='creator_op':
                    msg,ok,op=val
                    if self.creator_window and self.creator_window.winfo_exists(): self.creator_window._creator['done'](msg,ok and op=='write')
                elif typ=='creator_read':
                    if self.creator_window and self.creator_window.winfo_exists():
                        c=self.creator_window._creator; self.raw_log+=val
                        decoded=self._decode_ndef_output(val)
                        c['decoded'].update(decoded)
                        c['set_preview'](decoded['display'])
                        c['status'].config(text='✓ Existing NDEF read and decoded below.' if decoded.get('kind') else '✓ NDEF read. A friendly decode was not recognised; the returned data is shown below.')
                elif typ=='auth_done':
                    if getattr(self,'auth_window',None) and self.auth_window.winfo_exists():
                        out,err=val; a=self.auth_window._auth; a['state']['running']=False; a['progress'].stop(); a['auto'].config(state='normal',text='AUTO CHECK KEYS')
                        a['tech'].config(state='normal'); a['tech'].delete('1.0','end'); a['tech'].insert('1.0',ANSI_RE.sub('',out) if out else (err or 'No output returned.')); a['tech'].config(state='disabled')
                        if err:
                            a['progress_title'].config(text='CHECK FAILED'); a['progress_detail'].config(text='The Proxmark3 command did not complete. See Technical Details below.'); a['result_title'].config(text='Could not complete key check'); a['result_detail'].config(text=err); a['facts']['ACCESS'].config(text='Check failed')
                        else:
                            clean=ANSI_RE.sub('',out); low=clean.lower()
                            keys=set(re.findall(r'(?i)(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])',clean))
                            found=bool(re.search(r'(?i)(found|valid|key\s*[ab]|\+---)',clean)) and ('no keys found' not in low)
                            if found:
                                a['progress_title'].config(text='CHECK COMPLETE'); a['progress_detail'].config(text='Proxmark3 finished the authentication check.'); a['result_title'].config(text='✓ Authentication results found'); a['result_detail'].config(text=(f'{len(keys)} unique 6-byte key value(s) appeared in the result. Review the sector map in Technical Details below.' if keys else 'Default-key authentication results were returned. Review the sector map in Technical Details below.')); a['facts']['ACCESS'].config(text='Results found')
                            else:
                                a['progress_title'].config(text='CHECK COMPLETE'); a['progress_detail'].config(text='No standard/default key was confirmed by this check.'); a['result_title'].config(text='No default key confirmed'); a['result_detail'].config(text='The card was detected, but this standard key check did not confirm access. No write was performed.'); a['facts']['ACCESS'].config(text='No default key')
                elif typ=='command_error':
                    cmd,err,raw=val; self.busy=False; self.scan_btn.config(state='normal'); getattr(self,'scan_hf_btn',self.scan_btn).config(state='normal'); self.connect_btn.config(state='normal'); self.status.config(text='●  CONNECTED' if self.connected else '●  ERROR')
                    title,detail=self.explain_failure(cmd,raw,err); self.activity.config(text='✗ '+title); self.finish_operation_progress(title,False); self._activity_feedback(title,detail if 'detail' in locals() else 'Operation failed.',False,False); self.stop_btn.config(state='disabled'); self._record_history(self.friendly_operation(cmd),self.last_tag,False)
                    if not self.raw_mode:self.show_clean_failure(title,detail)
                elif typ=='error':
                    self.busy=False;self.scan_btn.config(state='normal');getattr(self,'scan_hf_btn',self.scan_btn).config(state='normal');self.connect_btn.config(state='normal');self.status.config(text='●  CONNECTED' if self.connected else '●  ERROR');messagebox.showerror(APP,val)
        except queue.Empty:pass
        self.after(50,self._drain)
    def _request_close(self):
        """Close the visible GUI immediately, then clean up owned processes."""
        if getattr(self,'_closing',False): return
        self._closing=True
        try:
            self.settings['raw_console']=self.raw_mode; self.settings['backup_dir']=self.library_root; self.settings['linked_backup_folders']=self.linked_backup_folders; self._save_json(self.settings_path,self.settings)
        except Exception: pass
        # Hide the GUI immediately so USB cleanup never makes the title-bar X look broken.
        try:
            self.withdraw(); self.update_idletasks()
        except Exception: pass
        # Destroy Tk immediately. Hardware/process cleanup runs independently so a
        # slow USB stack can never make the title-bar X look broken.
        def cleanup_devices():
            try: self.pm.stop()
            except Exception: pass
        threading.Thread(target=cleanup_devices,daemon=True).start()
        p=getattr(self,'update_proc',None)
        if p and p.poll() is None:
            try: p.terminate()
            except Exception: pass
        self.update_proc=None
        try: tk.Tk.destroy(self)
        except Exception: pass

    def destroy(self):
        # Route programmatic closes through the same title-bar-X path.
        self._request_close()

if __name__=='__main__':App().mainloop()
