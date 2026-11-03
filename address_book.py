"""An editable address field backed by the app's shared saved IP list."""
from tkinter import ttk


class SavedAddressCombobox(ttk.Combobox):
    def __init__(self, parent, app, **kwargs):
        self.app = app
        kwargs.pop('show', None)
        super().__init__(parent, postcommand=self.refresh_addresses, **kwargs)
        self.refresh_addresses()
        self.bind('<<ComboboxSelected>>', lambda _: self.state(['!invalid']), add='+')

    def refresh_addresses(self):
        preferences = getattr(self.app, 'preferences', None)
        self.configure(values=preferences.addresses if preferences is not None else ())


def refresh_address_fields(widget):
    if isinstance(widget, SavedAddressCombobox):
        widget.refresh_addresses()
    for child in widget.winfo_children():
        refresh_address_fields(child)
