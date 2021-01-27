#    This file is part of Korman.
#
#    Korman is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    Korman is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with Korman.  If not, see <http://www.gnu.org/licenses/>.

debug = True

import bpy
from . import addon_prefs
from . import exporter, render
from . import properties, ui
from . import nodes
from . import operators

from . import auto_load

bl_info = {
    "name":        "Korman",
    "author":      "Guild of Writers",
    "blender":     (2, 83, 0),
    "location":    "File > Import-Export",
    "description": "Exporter for Cyan Worlds' Plasma Engine",
    "warning":     "beta",
    "category":    "System",
}

auto_load.init()

def register():
    """Registers all Blender operators and GUI items in Korman"""
    auto_load.register()
#    if debug: print(dir(bpy.utils))
#    bpy.utils.register_class(nodes.PlasmaNodeItem)
#    #nodes.register()
#    operators.register()
#    properties.register()
#    ui.register()


def unregister():
    """Unregisters all Blender operators and GUI items"""
    auto_load.register()
#    bpy.utils.unregister_class(nodes.PlasmaNodeItem)
#    #nodes.unregister()
#    operators.unregister()
#    properties.unregister()
#    ui.unregister()


if __name__ == "__main__":
    register()
