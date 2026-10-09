"""Published robot sizes, checked against UR sources on 2026-10-09.

Reach is a manufacturer's envelope rating, not a DH link length. New Gen 7
marketing names stay reference-only until their own geometry is verified.
"""
from dataclasses import dataclass

from kinematics import MODELS

LINEUP_SOURCE = 'https://www.universal-robots.com/products/robot-arms/'
DESCRIPTION_SOURCE = 'https://github.com/UniversalRobots/Universal_Robots_ROS2_Description/tree/6662e15f32c23c12ece57d0050aee9a716e4fc41/config'


@dataclass(frozen=True)
class RobotSize:
    family: str
    reach_mm: int
    payload: str
    source: str
    note: str = ''


def datasheet(model):
    return f'https://www.universal-robots.com/manuals/latest/en/datasheets/{model.lower()}/'


ROBOT_SIZES = {
    'UR3': RobotSize('CB-Series', 500, '3 kg', 'https://www.universal-robots.com/media/1801258/eng_199901_ur_main-product_brochure_web_1.pdf'),
    'UR5': RobotSize('CB-Series', 850, '5 kg', 'https://www.universal-robots.com/media/1801258/eng_199901_ur_main-product_brochure_web_1.pdf'),
    'UR10': RobotSize('CB-Series', 1300, '10 kg', 'https://www.universal-robots.com/media/1801258/eng_199901_ur_main-product_brochure_web_1.pdf'),
    'UR3e': RobotSize('e-Series', 500, '3 kg', datasheet('ur3e')),
    'UR5e': RobotSize('e-Series', 850, '5 kg', datasheet('ur5e')),
    'UR7e': RobotSize('e-Series', 850, '7.5 kg', datasheet('ur7e')),
    'UR10e': RobotSize('e-Series', 1300, '10 / 12.5 kg', datasheet('ur10e'), 'Payload depends on robot hardware revision; confirm your controller rating.'),
    'UR12e': RobotSize('e-Series', 1300, '12.5 kg', datasheet('ur12e')),
    'UR16e': RobotSize('e-Series', 900, '16 kg', datasheet('ur16e')),
    'UR8 Long': RobotSize('UR Series', 1750, '8 kg · 10 extended', datasheet('ur8long')),
    'UR15': RobotSize('UR Series', 1300, '15 kg · 17.5 extended', datasheet('ur15')),
    'UR18': RobotSize('UR Series', 950, '18 kg', datasheet('ur18')),
    'UR20': RobotSize('UR Series', 1750, '20 kg · 25 extended', datasheet('ur20')),
    'UR30': RobotSize('UR Series', 1300, '30 kg · 35 extended', datasheet('ur30')),
}
for name, reach, payload in (
    ('UR3e-540', 540, '3 kg'), ('UR7e-920', 920, '7.5 kg'),
    ('UR12e-1300', 1300, '12.5 kg'), ('UR10g-1750', 1750, '10 kg'),
    ('UR17g-1300', 1300, '17.5 kg'), ('UR18g-950', 950, '18 kg'),
    ('UR20-1750', 1750, '20 kg · 25 extended'), ('UR30-1300', 1300, '30 kg · 35 extended'),
):
    note = 'Listed in the current UR lineup. Model-specific kinematics and meshes are not in the verified official description release; reference only.'
    if name in ('UR3e-540', 'UR7e-920'):
        note += ' The product-page reach differs from its linked legacy datasheet; the listing is shown here, without changing the legacy geometry.'
    ROBOT_SIZES[name] = RobotSize('Gen 7', reach, payload,
                                f'https://www.universal-robots.com/products/{name.lower()}/', note)


def model_summary(model):
    spec = ROBOT_SIZES[model]
    return f'{spec.family}  ·  {spec.reach_mm:,} mm reach  ·  {spec.payload}'


def show_catalog(parent, selected_model):
    import tkinter as tk
    from tkinter import ttk
    window = tk.Toplevel(parent)
    window.title('Robot sizes · UR Motion Studio')
    window.geometry('960x700')
    window.minsize(820, 580)
    window.transient(parent)
    panel = ttk.Frame(window, padding=24)
    panel.pack(fill='both', expand=True)
    ttk.Label(panel, text='Find your robot', style='Title.TLabel').pack(anchor='w')
    ttk.Label(panel, text='Published reach and payload · 14 models with verified 3D geometry · Checked 9 Oct 2026',
              style='Muted.TLabel').pack(anchor='w', pady=(6, 18))
    frame = ttk.Frame(panel)
    frame.pack(fill='both', expand=True)
    tree = ttk.Treeview(frame, columns=('model', 'family', 'reach', 'payload', 'geometry'), show='headings', selectmode='browse')
    for name, label, width in [('model', 'Model', 140), ('family', 'Family', 110), ('reach', 'Reach', 100),
                               ('payload', 'Rated payload', 190), ('geometry', 'Simulator', 160)]:
        tree.heading(name, text=label)
        tree.column(name, width=width, minwidth=75)
    tree.pack(side='left', fill='both', expand=True)
    scrollbar = ttk.Scrollbar(frame, command=tree.yview)
    scrollbar.pack(side='right', fill='y')
    tree.configure(yscrollcommand=scrollbar.set)
    for model, spec in ROBOT_SIZES.items():
        tree.insert('', 'end', iid=model, values=(model, spec.family, f'{spec.reach_mm:,} mm', spec.payload,
                                                 'Available' if model in MODELS else 'Reference only'))
    details = tk.StringVar()
    ttk.Label(panel, textvariable=details, wraplength=860, style='Muted.TLabel').pack(fill='x', pady=(16, 8))
    source = ttk.Entry(panel, state='readonly')
    source_text = tk.StringVar()
    source.configure(textvariable=source_text)
    source.pack(fill='x')
    actions = ttk.Frame(panel)
    actions.pack(fill='x', pady=(16, 0))

    def use(_event=None):
        names = tree.selection()
        if names and names[0] in MODELS:
            selected_model.set(names[0])
            window.destroy()

    choose = ttk.Button(actions, text='Use selected model', style='Accent.TButton', command=use)
    choose.pack(side='right')
    ttk.Button(actions, text='Close', command=window.destroy).pack(side='left')

    def changed(_event=None):
        names = tree.selection()
        if not names:
            return
        model = names[0]
        spec = ROBOT_SIZES[model]
        details.set(spec.note or 'Nominal model geometry. Extended payload ratings depend on manufacturer conditions and controller configuration.')
        source_text.set(spec.source)
        choose.configure(state='normal' if model in MODELS else 'disabled')

    tree.bind('<<TreeviewSelect>>', changed)
    tree.bind('<Double-1>', use)
    tree.selection_set(selected_model.get())
    tree.see(selected_model.get())
    changed()
    return window
