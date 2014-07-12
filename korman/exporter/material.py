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

import bpy
import bgl
import math
from PyHSPlasma import *
import weakref

from . import explosions
from . import utils

# BGL doesn't know about this as of Blender 2.71
bgl.GL_GENERATE_MIPMAP = 0x8191

class _GLTexture:
    def __init__(self, blimg):
        self._ownit = (blimg.bindcode == 0)
        if self._ownit:
            if blimg.gl_load() != 0:
                raise explosions.GLLoadError(blimg)
        self._blimg = blimg

    def __del__(self):
        if self._ownit:
            self._blimg.gl_free()

    def __enter__(self):
        """Sets the Blender Image as the active OpenGL texture"""
        self._previous_texture = self._get_integer(bgl.GL_TEXTURE_BINDING_2D)
        self._changed_state = (self._previous_texture != self._blimg.bindcode)
        if self._changed_state:
            bgl.glBindTexture(bgl.GL_TEXTURE_2D, self._blimg.bindcode)
        return self

    def __exit__(self, type, value, traceback):
        mipmap_state = getattr(self, "_mipmap_state", None)
        if mipmap_state is not None:
            bgl.glTexParameteri(bgl.GL_TEXTURE_2D, bgl.GL_GENERATE_MIPMAP, mipmap_state)

        if self._changed_state:
            bgl.glBindTexture(bgl.GL_TEXTURE_2D, self._previous_texture)

    def generate_mipmap(self):
        """Generates all mip levels for this texture"""
        self._mipmap_state = self._get_tex_param(bgl.GL_GENERATE_MIPMAP)

        # Note that this is a very old feature from OpenGL 1.x -- it's new enough that Windows (and
        # Blender apparently) don't support it natively and yet old enough that it was thrown away
        # in OpenGL 3.0. The new way is glGenerateMipmap, but Blender likes oldgl, so we don't have that
        # function available to us in BGL. I don't want to deal with loading the GL dll in ctypes on
        # many platforms right now (or context headaches). If someone wants to fix this, be my guest!
        # It will simplify our state tracking a bit.
        bgl.glTexParameteri(bgl.GL_TEXTURE_2D, bgl.GL_GENERATE_MIPMAP, 1)

    def get_level_data(self, level, calc_alpha=False):
        """Gets the uncompressed pixel data for a requested mip level, optionally calculating the alpha
           channel from the image color data
        """
        width = self._get_tex_param(bgl.GL_TEXTURE_WIDTH, level)
        height = self._get_tex_param(bgl.GL_TEXTURE_HEIGHT, level)

        # Grab the image data
        size = width * height * 4
        buf = bgl.Buffer(bgl.GL_BYTE, size)
        bgl.glGetTexImage(bgl.GL_TEXTURE_2D, level, bgl.GL_RGBA, bgl.GL_UNSIGNED_BYTE, buf);

        # Calculate le alphas
        if calc_alpha:
            for i in range(size, 4):
                base = i*4
                r, g, b = buf[base:base+2]
                buf[base+3] = int((r + g + b) / 3)
        return bytes(buf)

    def _get_integer(self, arg):
        buf = bgl.Buffer(bgl.GL_INT, 1)
        bgl.glGetIntegerv(arg, buf)
        return int(buf[0])

    def _get_tex_param(self, param, level=None):
        buf = bgl.Buffer(bgl.GL_INT, 1)
        if level is None:
            bgl.glGetTexParameteriv(bgl.GL_TEXTURE_2D, param, buf)
        else:
            bgl.glGetTexLevelParameteriv(bgl.GL_TEXTURE_2D, level, param, buf)
        return int(buf[0])


class MaterialConverter:
    def __init__(self, exporter):
        self._exporter = weakref.ref(exporter)
        self._hsbitmaps = {}

    def export_material(self, bo, bm):
        """Exports a Blender Material as an hsGMaterial"""
        print("    Exporting Material '{}'".format(bm.name))

        hsgmat = self._mgr.add_object(hsGMaterial, name=bm.name, bl=bo)
        self._export_texture_slots(bo, bm, hsgmat)

        # Plasma makes several assumptions that every hsGMaterial has at least one layer. If this
        # material had no Textures, we will need to initialize a default layer
        if not hsgmat.layers:
            layer = self._mgr.add_object(plLayer, name="{}_AutoLayer".format(bm.name), bl=bo)
            self._propagate_material_settings(bm, layer)
            hsgmat.addLayer(layer.key)

        # Looks like we're done...
        return hsgmat.key

    def _export_texture_slots(self, bo, bm, hsgmat):
        for slot in bm.texture_slots:
            if slot is None or not slot.use:
                continue

            name = "{}_{}".format(bm.name, slot.name)
            print("        Exporting Plasma Layer '{}'".format(name))
            layer = self._mgr.add_object(plLayer, name=name, bl=bo)
            self._propagate_material_settings(bm, layer)

            # UVW Channel
            for i, uvchan in enumerate(bo.data.tessface_uv_textures):
                if uvchan.name == slot.uv_layer:
                    layer.UVWSrc = i
                    print("            Using UV Map #{} '{}'".format(i, name))
                    break
            else:
                print("            No UVMap specified... Blindly using the first one, maybe it exists :|")

            # General texture flags and such
            texture = slot.texture
            # ...

            # Export the specific texture type
            export_fn = "_export_texture_type_{}".format(texture.type.lower())
            if not hasattr(self, export_fn):
                raise explosions.UnsupportedTextureError(texture, bm)
            getattr(self, export_fn)(bo, hsgmat, layer, texture)
            hsgmat.addLayer(layer.key)

    def _export_texture_type_image(self, bo, hsgmat, layer, texture):
        """Exports a Blender ImageTexture to a plLayer"""

        # First, let's apply any relevant flags
        state = layer.state
        if texture.invert_alpha:
            state.blendFlags |= hsGMatState.kBlendInvertAlpha

        # Now, let's export the plBitmap
        # If the image is None (no image applied in Blender), we assume this is a plDynamicTextMap
        # Otherwise, we create a plMipmap and call into korlib to export the pixel data
        if texture.image is None:
            bitmap = self.add_object(plDynamicTextMap, name="{}_DynText".format(layer.key.name), bl=bo)
        else:
            # blender likes to create lots of spurious .0000001 objects :/
            name = texture.image.name
            name = name[:name.find('.')]
            if texture.use_mipmap:
                name = "{}.dds".format(name)
            else:
                name = "{}.bmp".format(name)

            if name in self._hsbitmaps:
                # well, that was easy...
                print("            Using '{}'".format(name))
                layer.texture = self._hsbitmaps[name].key
                return
            else:
                location = self._mgr.get_textures_page(bo)
                bitmap = self._TEMP_export_image(bo, name, texture)

        # Store the created plBitmap and toss onto the layer
        self._hsbitmaps[name] = bitmap
        layer.texture = bitmap.key

    def _TEMP_export_image(self, bo, name, texture):
        print("            Exporting {}".format(name))

        image = texture.image
        oWidth, oHeight = image.size
        eWidth = int(round(pow(2, math.log(oWidth, 2))))
        eHeight = int(round(pow(2, math.log(oHeight, 2))))
        if (eWidth != oWidth) or (eHeight != oHeight):
            print("                Image is not a POT ({}x{}) resizing to {}x{}".format(oWidth, oHeight, eWidth, eHeight))
            image.scale(eWidth, eHeight)

        # Basic things
        levelHint = 0 if texture.use_mipmap else 1
        compression = plBitmap.kDirectXCompression if texture.use_mipmap else plBitmap.kUncompressed
        dxt = plBitmap.kDXT5 if texture.use_alpha or texture.use_calculate_alpha else plBitmap.kDXT1

        # This wraps the call to plMipmap::Create
        mipmap = plMipmap(name=name, width=eWidth, height=eHeight, numLevels=levelHint,
                          compType=compression, format=plBitmap.kRGB8888, dxtLevel=dxt)
        page = self._mgr.get_textures_page(bo)
        self._mgr.AddObject(page, mipmap)

        with _GLTexture(image) as glimage:
            if texture.use_mipmap:
                glimage.generate_mipmap()

            stuff_func = mipmap.CompressImage if compression == plBitmap.kDirectXCompression else mipmap.setLevel
            for i in range(mipmap.numLevels):
                data = glimage.get_level_data(i, texture.use_calculate_alpha)
                stuff_func(i, data)
        return mipmap


    def _export_texture_type_none(self, bo, hsgmat, layer, texture):
        # We'll allow this, just for sanity's sake...
        pass

    @property
    def _mgr(self):
        return self._exporter().mgr

    def _propagate_material_settings(self, bm, layer):
        """Converts settings from the Blender Material to corresponding plLayer settings"""
        state = layer.state

        # Shade Flags
        if not bm.use_mist:
            state.shadeFlags |= hsGMatState.kShadeNoFog # Dead in CWE
            state.shadeFlags |= hsGMatState.kShadeReallyNoFog

        # Colors
        layer.ambient = utils.color(bpy.context.scene.world.ambient_color)
        layer.preshade = utils.color(bm.diffuse_color)
        layer.runtime = utils.color(bm.diffuse_color)
        layer.specular = utils.color(bm.specular_color)
