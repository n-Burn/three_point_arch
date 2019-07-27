'''
BEGIN GPL LICENSE BLOCK

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software Foundation,
Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

END GPL LICENSE BLOCK

#============================================================================

 [Stage]   [Event that ends stage]
* Stage 0 - No points placed, add-on just launched and is initializing
* Stage 1 - 1st point placed
* Stage 2 - 2nd point placed (1st to 2nd point is arc width)
* Stage 3 - 3rd point placed (to create planar surface to align arc to)
* Stage 4 - 1st arch edge created
* Stage 5 - Make faces from 1st arch (with extrude > scale)
* Stage 6 - Make arch faces into solid (by extruding faces from Stage 5)
* Exit add-on

Note: When the add-on is running it will proceed through Stage 0 to Stage 1 
with no pause in execution. Likewise, once Stage 3 completes, the add-on 
will proceed through Stage 4 to Stage 5 with no pause in execution. 
Both of these previously mentioned stage intervals (0 to 1 and 3 to 4) 
can essentially be considered a single stage.

To-Do
[X] Option to change number of edges on arch (pause with space to break modal?)
[X] Option to show measurements?
[?] Better shortcut info, make sure helpdisplay up to date
[_] Option to change color scheme? NP Station color scheme?
[?] Better DPI argument handling for font drawing
[_] Make OBJECT / EDIT mode switching less error prone (wrap in function?)
[X] Find out why arch mesh isn't deleted if addon exited during extrudes
[?] Find way to display measurments for last 2 extrudes
[X] Make workaround in case space is pressed while extuding
[X] Add shadowing for measurement text

Possible To-Do
[_] Make a new basic text class and have HelpText inherit it?
[X] Turn distances and segment count display during pause into classes?
[_] Option to manually set distance between arch edges (spacebar pause menu?)
[_] Option to "roll back" arch distance?
[_] Option to add an arch "base/support wall" before/after creating arch?
[_] Option to change arch types (circular, equilateral, parabolic, etc)
[_] Use curves instead of vertex plotting?
[_] Option to have normals either inside or outside?
[_] Make previous measurments always visible (like with np_float_box)
[_] Add texture to arch before exiting?
[_] 
'''

bl_info = {
    "name": "Three Point Arch Tool",
    "author": "nBurn",
    "version": (0, 1, 2),
    "blender": (2, 80, 0),
    "location": "View3D > Tools Panel",
    "description": "Tool for creating arches",
    "category": "Mesh"
}

# Additional credits:
# Help \ shortcut menu system adapted from NP Station

from copy import deepcopy
from math import pi, degrees, radians, sin

import bpy
import bmesh
import bgl
import blf
import gpu
from mathutils import geometry, Quaternion, Vector
from bpy_extras import view3d_utils
from bpy_extras.view3d_utils import location_3d_to_region_2d as loc3d_to_reg2d
from bpy_extras.view3d_utils import region_2d_to_vector_3d as reg2d_to_vec3d
from bpy_extras.view3d_utils import region_2d_to_location_3d as reg2d_to_loc3d
from bpy_extras.view3d_utils import region_2d_to_origin_3d as reg2d_to_org3d
from bpy.props import IntProperty, BoolProperty
from gpu_extras.batch import batch_for_shader

#print("Loaded: Three Point Arc Tool\n")  # debug

# "Constant" values
(
    X,
    Y,
    Z,
    PLACE_1ST,
    PLACE_2ND,
    PLACE_3RD,
    ARCH_EXTRUDE_1,
    ARCH_EXTRUDE_2,
    EXIT
) = range(9)


def safe_loc3d_to_reg2d(reg, rv3d, pts):
    empty_list = True
    res = []
    for p in pts:
        p2d = loc3d_to_reg2d(reg, rv3d, p)
        if p2d is not None:
            if empty_list:
                empty_list = False
            res.append(p2d)
    if not empty_list:
        return res
    else:
        return None


class Colr:
    red   = 1.0, 0.0, 0.0, 0.5
    green = 0.0, 1.0, 0.0, 0.5
    blue  = 0.0, 0.0, 1.0, 0.5
    white = 1.0, 1.0, 1.0, 1.0
    grey  = 1.0, 1.0, 1.0, 0.4
    black = 0.0, 0.0, 0.0, 1.0
    brown = 0.15, 0.15, 0.15, 0.20


# Defines the settings part in the addons tab:
class TPARCH_prefs(bpy.types.AddonPreferences):
    bl_idname = __name__

    np_scale_dist: bpy.props.FloatProperty(
        name='',
        description='Distance multiplier (for example, for cm use 100)',
        default=100,
        min=0,
        step=100,
        precision=2)

    '''
    np_col_scheme: bpy.props.EnumProperty(
        name ='',
        items = (
            ('csc_default_grey', 'Blender_Default_NP_GREY',''),
            ('csc_school_marine', 'NP_school_paper_NP_MARINE',''),
            ('def_blender_gray', 'TP_Arch_Default_Theme','')),
        default = 'def_blender_gray',
        #default = 'csc_default_grey',
        description = 'Choose the overall addon color scheme, according to " + \
                "your current Blender theme')
    '''

    np_suffix_dist: bpy.props.EnumProperty(
        name='',
        items=(("'", "'", ''), ('"', '"', ''), (' thou', 'thou', ''),
               (' km', 'km', ''), (' m', 'm', ''), (' cm', 'cm', ''),
               (' mm', 'mm', ''), (' nm', 'nm', ''), ('None', 'None', '')),
        default=' cm',
        description='Add a unit extension after the numerical distance ')

    segm_cnt: IntProperty(
        name="Arch segments",
        description="Number of segments in arch",
        min=2,
        default=16)

    extr_enabled: BoolProperty(name="Enable extrude",
        description="Extrude arch after edge creation",
        default=True)

    def draw(self, context):
        layout = self.layout
        # split 50 / 50, then split 50 to 60 / 40
        row1 = layout.row()
        r1_sl = row1.split(factor=0.6)  # 60% of 50%
        r1_sl.label(text="Unit scale for distance")
        r1_sl.prop(self, "np_scale_dist")  # 40% of 50%
        r1_s2 = row1.split(factor=0.6)  # 60% of 50%
        r1_s2.label(text="Unit suffix for distance")
        r1_s2.prop(self, "np_suffix_dist")

        row2 = layout.row()
        r2_sl = row2.split(factor=0.5)
        r2_sl.prop(self, "segm_cnt")  # 50%
        r2_sl.prop(self, "extr_enabled", text="Enable extrude")
        #r2_sl_s = r2_sl.split(factor=0.3)
        #r2_sl_s.label(text="Color scheme")
        #r2_sl_s.prop(self, "np_col_scheme")

        #row3 = layout.row()
        #r3_sl = row3.split(factor=0.5)
        #r3_sl.prop(self, "extr_enabled", text="Enable extrude")


def backup_blender_settings():
    backup = [
        deepcopy(bpy.context.tool_settings.use_snap),
        deepcopy(bpy.context.tool_settings.snap_elements),
        deepcopy(bpy.context.tool_settings.snap_target),
        deepcopy(bpy.context.tool_settings.transform_pivot_point),
        deepcopy(bpy.context.scene.transform_orientation_slots[0].type),
        deepcopy(bpy.context.space_data.show_gizmo),
        deepcopy(bpy.context.scene.cursor.location),
        deepcopy(bpy.context.tool_settings.mesh_select_mode[:])]
    return backup


def init_blender_settings():
    bpy.context.tool_settings.use_snap = False
    bpy.context.tool_settings.snap_elements = {'VERTEX'}
    bpy.context.tool_settings.snap_target = 'CLOSEST'
    bpy.context.tool_settings.transform_pivot_point = 'ACTIVE_ELEMENT'
    bpy.context.scene.transform_orientation_slots[0].type = 'GLOBAL'
    bpy.context.space_data.show_gizmo = False
    bpy.context.tool_settings.mesh_select_mode = True, False, False
    return


def restore_blender_settings(backup):
    bpy.context.tool_settings.use_snap = deepcopy(backup[0])
    bpy.context.tool_settings.snap_elements = deepcopy(backup[1])
    bpy.context.tool_settings.snap_target = deepcopy(backup[2])
    bpy.context.tool_settings.transform_pivot_point = deepcopy(backup[3])
    bpy.context.scene.transform_orientation_slots[0].type = deepcopy(backup[4])
    bpy.context.space_data.show_gizmo = deepcopy(backup[5])
    bpy.context.scene.cursor.location = deepcopy(backup[6])
    bpy.context.tool_settings.mesh_select_mode = deepcopy(backup[7])
    return


class DrawMeanDistance:
    def __init__(self, sz, settings):
        self.reg = bpy.context.region
        self.rv3d = bpy.context.region_data
        self.dpi = bpy.context.preferences.system.dpi
        self.size = sz
        self.txtcolr = settings["col_num_main"]
        self.shdcolr = settings["col_num_shadow"]
        self.shdoffs = -1, -1  # shadow offset
        self.font_id = 0

    def draw(self, pts, meas_mult, meas_suff):
        pts_3d = []
        for i in range(len(pts)):
            if type(pts[i]) is not Vector:
                pts_3d.append(Vector(pts[i]))
            else:
                pts_3d.append(pts[i])

        p1_2d = loc3d_to_reg2d(self.reg, self.rv3d, pts_3d[0])
        p2_2d = loc3d_to_reg2d(self.reg, self.rv3d, pts_3d[1])

        draw_line_2D(p1_2d, p2_2d, Colr.white)

        if p1_2d is None or p2_2d is None:
            p1_2d = p2_2d = 0.0, 0.0

        def get_pts_mean(locs2d, max_val):
            res = 0
            for i in locs2d:
                if i > max_val:
                    res += max_val
                elif i > 0:
                    res += i
            return res / 2

        mean_x = get_pts_mean((p1_2d[X], p2_2d[X]), self.reg.width)
        mean_y = get_pts_mean((p1_2d[Y], p2_2d[Y]), self.reg.height)
        offset = 5, 5
        shdblr = 3  # shadow blur
        dist_loc = mean_x + offset[X], mean_y + offset[Y]
        dist_3d = meas_mult * (pts_3d[1] - pts_3d[0]).length
        dist_3d_rnd = abs(round(dist_3d, 2))
        dist = str(dist_3d_rnd) + meas_suff
        #print("self.txtcolr", self.txtcolr)  # debug

        if dist_3d_rnd != 0:
            blf.enable(self.font_id, blf.SHADOW)
            blf.shadow(self.font_id, shdblr, *self.shdcolr)
            blf.shadow_offset(self.font_id, *self.shdoffs)

            #bgl.glColor4f(*self.txtcolr)
            blf.color(self.font_id, *self.txtcolr)
            blf.size(self.font_id, self.size, self.dpi)
            blf.position(self.font_id, dist_loc[0], dist_loc[1], 0)
            blf.draw(self.font_id, dist)

            blf.disable(self.font_id, blf.SHADOW)

        # To-Do : if displaying measurements for multiple dimensions
        # at once is needed, may have to uncomment below return
        # statement so measuresments can be stored externally.
        #return (dist_3d_rnd, dist)


class DrawSegmCounter:
    def __init__(self, settings):
        self.dpi = bpy.context.preferences.system.dpi
        self.desc_str = "Arch segments"
        self.desc_size = 16
        self.seg_cnt_size = 32
        self.desc_co_offs = None
        self.seg_cnt_co_offs = None
        self.desc_colr = settings["col_font_instruct_main"]
        self.seg_cnt_colr = settings["col_font_instruct_main"]
        self.shdcolr = settings["col_font_instruct_shadow"]
        self.shdoffs = -1, -1  # shadow offset
        self.font_id = 0

        d_b_offs =  Vector((10, 24))  # description base offset
        c_b_offs =  Vector((5, 6))  # count base offset
        blf.size(self.font_id, self.desc_size, self.dpi)
        desc_dim = Vector(blf.dimensions(self.font_id, self.desc_str))

        #desc_x_hf =
        self.desc_co_offs = Vector((-desc_dim[X] - d_b_offs[X], d_b_offs[Y]))
        self.seg_cnt_co_offs = Vector((c_b_offs[X], desc_dim[Y] + c_b_offs[Y]))

    def draw(self, cnt, co):
        #if co is None:
        #    return
        desc_co = co + self.desc_co_offs
        seg_cnt_co = desc_co + self.seg_cnt_co_offs

        #bgl.glColor4f(*self.desc_colr)
        blf.color(self.font_id, *self.desc_colr)
        blf.size(self.font_id, self.desc_size, self.dpi)
        blf.position(self.font_id, desc_co[0], desc_co[1], 0)
        blf.draw(self.font_id, self.desc_str)

        #bgl.glColor4f(*self.seg_cnt_colr)
        blf.color(self.font_id, *self.seg_cnt_colr)
        blf.size(self.font_id, self.seg_cnt_size, self.dpi)
        blf.position(self.font_id, seg_cnt_co[0], seg_cnt_co[1], 0)
        blf.draw(self.font_id, str(cnt))


class HelpText:
    def get_size(self):
        blf.size(self.font_id, self.size, self.dpi)
        self.wid, self.hgt = blf.dimensions(self.font_id, self.origtxt)

    def __init__(self, text, size, h_a, colr, shdcolr):
        self.dpi = bpy.context.preferences.system.dpi
        self.origtxt = text  # original text string
        self.disptxt = [text]  # displayed text
        self.size = size
        self.colr = colr  # color / colour
        self.shad = False  # text shadow enabled?
        self.shad_colr = shdcolr
        #self.horz_aln = h_a  # horizontal alignment (L/R/C, C only for instr)
        self.wid = 0  # width
        self.hgt = 0  # height
        self.font_id = 0
        self.crop = False
        self.viz = True  # visible
        self.pos = []  # position
        self.ovrfl_pos = []
        self.ovrfl = False

        self.get_size()


    def draw_wrapper(self):
        if self.viz:
            if self.shad:
                shdblur = 3  # shadow blur
                shdoffs = -1, -1
                blf.enable(self.font_id, blf.SHADOW)
                blf.shadow(self.font_id, shdblur, *self.shad_colr)
                blf.shadow_offset(self.font_id, shdoffs[X], shdoffs[Y])
                self.draw()
                blf.disable(self.font_id, blf.SHADOW)
            else:
                self.draw()

    def draw(self):
        #bgl.glColor4f(*self.colr)
        blf.color(self.font_id, *self.colr)
        blf.size(self.font_id, self.size, self.dpi)
        p1 = self.pos
        blf.position(self.font_id, p1[0], p1[1], 0)
        blf.draw(self.font_id, self.disptxt[0])
        if self.ovrfl:
            p2 = self.ovrfl_pos
            blf.position(self.font_id, p2[0], p2[1], 0)
            blf.draw(self.font_id, self.disptxt[1])


class HelpBar:
    #def __init__(self, col , disp=True):
    def __init__(self):
        #self.disp_bar = False # display bar?
        self.colr = None  # bar color
        self.help_txts = []  # help text objects
        self.txtcnt = 0  # text count
        self.barcnt = 1  # bar count
        #self.wid = 0  # bar width
        self.max_txt_wid = 0  # max text width
        self.hgt = 0  # bar height
        self.bndry = [ [], [] ]  # bar positions
        self.viz = True  # is bar or its contents visible
        self.x_off = 0  # x offset
        self.y_off = 0
        self.bar_y_off = 3  # y offset
        self.extend = False
        #self.crop = False

    def clear(self):
        self.help_txts = []
        self.txtcnt = 0
        self.barcnt = 1
        self.extend = False

    def set_sizes(self, barwid):
        if self.help_txts != []:
            dpi = self.help_txts[0].dpi
            font_id = 0
            sizes = [i.size for i in self.help_txts]
            max_size = max(sizes)
            blf.size(font_id, max_size, dpi)
            x, max_y =  blf.dimensions(0, "Tgp")
            y = blf.dimensions(0, "T")[1]
            hgt_mult = 1.6  # multiplier for bar heigt
            self.hgt = int(max_y * hgt_mult)
            self.x_off = int(x / 2)
            hgtoff_mult = (2 - hgt_mult) / 2
            self.y_off = int((max_y * hgtoff_mult) + (max_y - y))
            self.max_txt_wid = barwid - (self.x_off * 2)

            # make sure text fits, if not don't display
            tot_width = 0
            for i in self.help_txts:
                tot_width += i.wid
                i.viz = True
            if tot_width > self.max_txt_wid:
                for i in self.help_txts:
                    if i.wid < int(self.max_txt_wid / 2):
                        i.viz = True
                    else:
                        i.vis = False

            '''
            tot_w = 0
            for i in self.help_txts: tot_w += i.wid + self.x_off
            if tot_w > self.max_txt_wid:
                self.extend = True
                if tot_w > (self.max_txt_wid * 2):
                    self.crop = True
            '''

    def fit(self):
        helptxt = self.help_txts[0]
        helptxt.viz = True
        dpi = self.help_txts[0].dpi
        if helptxt.wid < self.max_txt_wid:
            self.barcnt = 1
            helptxt.disptxt = [helptxt.origtxt]
            helptxt.ovrfl = False
            self.extend = False
        else:
            font_id = helptxt.font_id
            str_segs = helptxt.origtxt.split(',')
            out_str = ['']
            str_cnt = 0  # text string count
            seg_cnt = len(str_segs)
            re_add = ([','] * (seg_cnt - 1)) + ['']
            blf.size(font_id, helptxt.size, dpi)
            blf_dim = blf.dimensions
            for i in range(seg_cnt):
                tmp_str = str_segs[i] + re_add[i]
                tmp_wid = blf_dim(font_id, tmp_str)[0]
                os_wid = blf_dim(font_id, out_str[str_cnt])[0]
                if (os_wid + tmp_wid) > self.max_txt_wid:
                    if str_cnt > 0:
                        break
                    else:
                        out_str.append('')
                        tmp_str = tmp_str.strip()
                        str_cnt += 1
                out_str[str_cnt] += tmp_str
            helptxt.disptxt = out_str
            self.barcnt = 2
            helptxt.ovrfl = True
            self.extend = True

    # get bar coordinates
    def get_bar_co(self, lf, ri, bt, tp):
        return [(lf, bt), (lf, tp), (ri, tp), (ri, bt)]

    def set_txt_pos(self, bx_lf, bx_ri, bx_bt):
        if self.txtcnt > 0:  # left align
            self.help_txts[0].pos = bx_lf + self.x_off, bx_bt + self.y_off
        if self.txtcnt > 1:  # right align
            ht_1_wid = self.help_txts[1].wid
            self.help_txts[1].pos = bx_ri - self.x_off - ht_1_wid, bx_bt + self.y_off
        if self.extend:
            self.help_txts[0].ovrfl_pos = self.help_txts[0].pos
            bx_bt2 = bx_bt + self.hgt + self.bar_y_off
            self.help_txts[0].pos = bx_lf + self.x_off, bx_bt2 + self.y_off

    def set_co(self, left, right, btm):  # bndry = boundary
        top = btm + self.hgt
        self.bndry[0] = self.get_bar_co(left, right, btm, top)
        self.set_txt_pos(left, right, btm)
        if self.extend:
            btm2 = top + self.bar_y_off
            top2 = btm2 + self.hgt
            self.bndry[1] = self.get_bar_co(left, right, btm2, top2)

    def draw(self):
        for b in range(self.barcnt):
            indc = (0, 1, 2), (2, 3, 0)
            shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
            batch = batch_for_shader(shader, 'TRIS', {"pos": self.bndry[b]}, indices=indc)
            shader.bind()
            shader.uniform_float("color", self.colr)
            batch.draw(shader)
        for i in self.help_txts:
            i.draw_wrapper()
        '''
        bgl.glColor4f(*self.colr)
        for b in range(self.barcnt):
            bgl.glBegin(bgl.GL_TRIANGLE_FAN)
            for co in self.bndry[b]:
                bgl.glVertex2f(*co)
            bgl.glEnd()

        for i in self.help_txts:
            i.draw_wrapper()
        '''


class HelpDisplay:
    def __init__(self, reg, settings):
        self.reg = reg  # region tools width
        self.rtoolsw = 0  # region tools width
        self.ruiw = 0  # region UI width
        self.rgwid = 0  # region width
        self.rghgt = 0  # region width
        #self.barcnt = 2  # bar count
        #self.siz = None  # text sizes
        #self.txtcolr = None  # text colors
        self.pos = None  # text positions
        self.instr = None  # instructions
        self.bartop = HelpBar()  # bar top
        self.barbot = HelpBar()
        self.bar_w = 0  # bar width
        self.viz = False  # is gui visible?
        self.dispbars = False  # display bars?
        self.settings = settings

        self.bartop.colr = self.settings["col_field_keys_aff"]
        self.barbot.colr = self.settings["col_field_keys_neg"]

    # always have to do full update after string change as
    # there is no guarantee string will not be split
    def clear_str(self):
        self.instr = None
        self.bartop.help_txts = []
        self.bartop.txtcnt = 0
        self.barbot.help_txts = []
        self.barbot.txtcnt = 0

    def add_str(self, help_typ, txt, size, align, colr=None, shdcolr=None):
        if help_typ == "INS":
            colr = self.settings["col_font_instruct_main"]
            shdcolr = self.settings["col_font_instruct_shadow"]
            self.instr = HelpText(
                txt, size, align, colr, shdcolr)
            self.instr.shad = True
        elif help_typ == "TOP":
            colr = self.settings["col_font_keys"]
            self.bartop.help_txts.append(HelpText(
                txt, size, align, colr, shdcolr))
            self.bartop.txtcnt += 1
        elif help_typ == "BOT":
            colr = self.settings["col_font_keys"]
            self.barbot.help_txts.append(HelpText(
                txt, size, align, colr, shdcolr))
            self.barbot.txtcnt += 1

    def new_vals(self):
        rtoolsw = 0
        ruiw = 0
        system = bpy.context.preferences.system
        if system.use_region_overlap:
            #if system.window_draw_method in ('TRIPLE_BUFFER', 'AUTOMATIC'):
            area = bpy.context.area
            for r in area.regions:
                if r.type == 'TOOLS':
                    rtoolsw = r.width
                elif r.type == 'UI':
                    ruiw = r.width

        if self.rtoolsw != rtoolsw or self.ruiw != ruiw or \
                self.rgwid != self.reg.width or self.rghgt != self.reg.height:
            self.rtoolsw = rtoolsw
            self.ruiw = ruiw
            self.rgwid = self.reg.width
            self.rghgt = self.reg.height
            return True
        else:
            return False

    def update(self):
        logo_w = 30
        reg_w, reg_h = self.rgwid, self.rghgt
        r_tools_w, r_ui_w = self.rtoolsw, self.ruiw
        offs_x = 60  # to avoid blocking xyz graphic (110 on tablet, desk 60)
        offs_y = 40  # 46
        bar_bar_y_offs = 3
        min_view_w = logo_w * 5  # view3d width minimum
        min_view_h = logo_w * 5  # view3d height minimum
        min_gui_w = 480  # bar width minimum
        min_gui_h = 280  # bar height minimum
        view_w = reg_w - r_tools_w - r_ui_w
        self.bar_w = (view_w - offs_x) * 0.96

        if view_w > min_view_w and reg_h > min_view_h:
            self.viz = True
            if view_w > min_gui_w and reg_h > min_gui_h:
                self.dispbars = True

                # set bar properties
                for i in (self.bartop, self.barbot):
                    i.set_sizes(self.bar_w)
                self.bartop.fit()

                left_brdr = r_tools_w + offs_x  # left border
                bar_x_beg = int(left_brdr + ((view_w - self.bar_w - offs_x) / 2))
                bar_x_end = bar_x_beg + self.bar_w
                self.barbot.set_co(bar_x_beg, bar_x_end, offs_y)

                barbot_y = self.barbot.bndry[0][1][1] + bar_bar_y_offs
                self.bartop.set_co(bar_x_beg, bar_x_end, barbot_y)

                # set instructions properties
                instr_max = (view_w - logo_w) * 0.9
                if self.instr.wid < instr_max:
                    self.instr.viz = True
                else:
                    self.instr.viz = False

                top_of_top_bar = self.bartop.bndry[0][1][1]
                instr_x = r_tools_w + view_w / 2 - self.instr.wid / 2 + logo_w / 2
                self.instr.pos = [
                    int(instr_x),
                    int(top_of_top_bar + (self.bartop.hgt * 2))]

            else:
                self.dispbars = False

        else:
            self.viz = False

    def draw(self):
        if self.new_vals():
            self.update()
        if self.viz:
            #font_id = 0
            #draw_logo()

            if self.dispbars:
                self.instr.draw_wrapper()
                self.bartop.draw()
                self.barbot.draw()


def get_rotated_pt(piv_co, mov_co, ang_rad, piv_norm):
    mov_aligned = mov_co - piv_co
    rot_val = Quaternion(piv_norm, ang_rad)
    mov_aligned.rotate(rot_val)
    return mov_aligned + piv_co


def draw_pt_2D(pt_co, pt_color):
    if pt_co is not None:
        bgl.glPointSize(10)
        shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'POINTS', {"pos": pt_co})
        shader.bind()
        shader.uniform_float("color", pt_color)
        batch.draw(shader)
        bgl.glPointSize(1)
        '''
        bgl.glEnable(bgl.GL_BLEND)
        bgl.glPointSize(10)
        bgl.glColor4f(*pt_color)
        bgl.glBegin(bgl.GL_POINTS)
        bgl.glVertex2f(*pt_co)
        bgl.glEnd()
        '''
    return


def draw_line_2D(pt_co_1, pt_co_2, pt_color):
    if None not in (pt_co_1, pt_co_2):
        coords = [pt_co_1, pt_co_2]
        shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'LINES', {"pos": coords})
        shader.bind()
        shader.uniform_float("color", pt_color)
        batch.draw(shader)
        '''
        bgl.glEnable(bgl.GL_BLEND)
        bgl.glPointSize(7)
        bgl.glColor4f(*pt_color)
        bgl.glBegin(bgl.GL_LINE_STRIP)
        bgl.glVertex2f(*pt_co_1)
        bgl.glVertex2f(*pt_co_2)
        bgl.glEnd()
        '''
    return


def draw_circ_arch_3D(steps, pts, orig, ang_meas, piv_norm, color, reg, rv3d):
    orig2d = safe_loc3d_to_reg2d(reg, rv3d, [orig])
    draw_pt_2D(orig2d, Colr.white)
    ang_incr = abs(ang_meas / steps)
    #bgl.glColor4f(*color)
    #bgl.glBegin(bgl.GL_LINE_STRIP)
    curr_ang = 0.0
    coords = []
    while curr_ang <= ang_meas:
        new_pt = get_rotated_pt(orig, pts[0], curr_ang, piv_norm)
        new_pt2d = loc3d_to_reg2d(reg, rv3d, new_pt)
        if new_pt2d is not None:
            coords.append(new_pt2d)
        curr_ang = curr_ang + ang_incr
    new_pt2d = loc3d_to_reg2d(reg, rv3d, pts[1])
    if new_pt2d is not None:
        coords.append(new_pt2d)
    len_coords = len(coords)
    if len_coords > 1:
        indc = [(i, i+1) for i in range(len_coords) if i+1 != len_coords]
        shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'LINES', {"pos": coords}, indices=indc)
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
    return
    '''
    orig2d = loc3d_to_reg2d(reg, rv3d, orig)
    # returns None when 3d point is not inside active 3D View
    if orig2d is not None:
        draw_pt_2D(orig2d, Colr.white)
    ang_incr = abs(ang_meas / steps)
    bgl.glColor4f(*color)
    bgl.glBegin(bgl.GL_LINE_STRIP)
    curr_ang = 0.0
    while curr_ang <= ang_meas:
        new_pt = get_rotated_pt(orig, pts[0], curr_ang, piv_norm)
        new_pt2d = loc3d_to_reg2d(reg, rv3d, new_pt)
        if new_pt2d is not None:
            bgl.glVertex2f(new_pt2d[X], new_pt2d[Y])
        curr_ang = curr_ang + ang_incr
    new_pt2d = loc3d_to_reg2d(reg, rv3d, pts[1])
    if new_pt2d is not None:
        bgl.glVertex2f(new_pt2d[X], new_pt2d[Y])
    bgl.glEnd()
    return
    '''


# Refreshes mesh drawing in 3D view and updates mesh coordinate
# data so ref_pts are drawn at correct locations.
# Using editmode_toggle to do this seems hackish, but editmode_toggle seems
# to be the only thing that updates both drawing and coordinate info.
def editmode_refresh(ed_type):
    if ed_type == "EDIT_MESH":
        bpy.ops.object.editmode_toggle()
        bpy.ops.object.editmode_toggle()


def add_pt(self, co):
    self.pts.append(co)
    self.pt_cnt += 1


# === PointFind code ===
class SnapPoint():
    def __init__(self):
        self.mode = "OBJECT"
        self.point = None
        self.ob = bpy.context.scene.objects
        #self.pt_cnt = 0

    # todo : move outside SnapPoint ?
    def get_mouse_3d(self, mouse_loc):
        region = bpy.context.region
        rv3d = bpy.context.region_data

        # make sure converted mouse location is visible from the 3D view,
        # if not, use less accurate alternative for getting mouses 3D coordinates
        mouse_vec3d = reg2d_to_vec3d(region, rv3d, mouse_loc)
        enterloc = reg2d_to_loc3d(region, rv3d, mouse_loc, mouse_vec3d)
        test2d = loc3d_to_reg2d(region, rv3d, enterloc)
        if test2d is None:
            persp_md_fix = mouse_vec3d / 5
            enterloc = reg2d_to_org3d(region, rv3d, mouse_loc) + persp_md_fix

        return enterloc

    def create(self, ms_loc_2d, ed_type):
        ms_loc_3d = self.get_mouse_3d(ms_loc_2d)
        if ed_type == 'OBJECT':
            bpy.ops.object.add(type='MESH', location=ms_loc_3d)
            self.point = bpy.context.object
        bpy.ops.transform.translate('INVOKE_DEFAULT')

    # Makes sure only the "guide point" object or vert
    # added with create is grabbed.
    def grab(self, ed_type, sel_backup=None):
        if ed_type == 'OBJECT':
            bpy.ops.object.select_all(action='DESELECT')
            self.point.select_set(True)
        elif ed_type == 'EDIT_MESH':
            bpy.ops.mesh.select_all(action='DESELECT')
            bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
            bm.verts[-1].select = True
            editmode_refresh(ed_type)
        bpy.ops.transform.translate('INVOKE_DEFAULT')

    # todo : make "move then grab" function?
    # Makes sure only the "guide point" object or vert
    # added with create is grabbed.
    def mouse_grab(self, ms_loc_2d, ed_type, sel_backup=None):
        ms_loc_3d = self.get_mouse_3d(ms_loc_2d)
        if ed_type == 'OBJECT':
            bpy.ops.object.select_all(action='DESELECT')
            self.point.select_set(True)
            self.point.location = ms_loc_3d
            '''        '''
        elif ed_type == 'EDIT_MESH':
            bpy.ops.mesh.select_all(action='DESELECT')
            bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
            bm.verts[-1].select = True
            inver_mw = bpy.context.edit_object.matrix_world.inverted()
            local_co = inver_mw @ ms_loc_3d
            bm.verts[-1].co = local_co
            editmode_refresh(ed_type)

        #snap_co = self.get_co(ms_loc_2d)
        #print("dist moved:", (snap_co - ms_loc_3d).length)  # debug
        bpy.ops.transform.translate('INVOKE_DEFAULT')

    # Makes sure only the "guide point" object or vert
    # added with create is deleted.
    def remove(self, ed_type, sel_backup=None):
        if ed_type == 'OBJECT':
            bpy.ops.object.select_all(action='DESELECT')
            self.point.select_set(True)
            bpy.ops.object.delete()
            '''        '''
        elif ed_type == 'EDIT_MESH':
            bpy.ops.mesh.select_all(action='DESELECT')
            bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
            bm.verts[-1].select = True
            editmode_refresh(ed_type)
            bpy.ops.mesh.delete(type='VERT')

        self.point = None
        #sel_backup.restore_selected(ed_type)

    def get_co(self, ed_type):
        if self.mode == 'OBJECT':
            return self.point.location.copy()

    def move(self, ed_type, new_co):
        if ed_type == 'OBJECT':
            self.point.location = new_co.copy()


def exit_addon(self):
    if self.curr_ed_type == 'EDIT_MESH':
        bpy.ops.object.editmode_toggle()
        self.curr_ed_type = bpy.context.mode
    if self.force_quit:
        self.force_quit = False
        self.snap.remove(self.curr_ed_type, self.sel_backup)
    #print("self.curr_ed_type", self.curr_ed_type)  # debug
    #print("self.stage", self.stage)  # debug
    #print("self.force_quit", self.force_quit)  # debug
    restore_blender_settings(self.settings_backup)
    #print("\n\nAdd-On Exited!\n")  # debug


def warp_cursor(self, context, dest_co):
    if dest_co is None:
        return
    area = context.area
    win = None
    for r in area.regions:
        if r.type == "WINDOW":
            win = r
            break
    if win is not None:
        warpco = dest_co[0] + win.x, dest_co[1] + win.y
        context.window.cursor_warp(*warpco)


# called when self.stage == PLACE_3RD
def update_arch(self, snap):
    if self.paused:
        return

    self.piv_norm = geometry.normal(self.pts[0], self.cent, snap)

    # exit function if piv_norm or snap have values that prevent rotations
    if self.piv_norm == Vector() or snap in self.pts:
        self.bad_input = True
        return
    else:
        self.bad_input = False

    # create pos and neg endpoints for determining where to create arch
    rot_pos, rot_neg = self.mov_aligned.copy(), self.mov_aligned.copy()
    rot_pos.rotate(Quaternion(self.piv_norm, self.rad90))
    rot_neg.rotate(Quaternion(self.piv_norm,-self.rad90))
    rot_pos = rot_pos + self.cent
    rot_neg = rot_neg + self.cent

    hgt = (snap - self.cent).length
    radius = None
    if hgt != 0:
        radius = (hgt / 2) + (self.wid**2) / (8 * hgt)
    else:
        radius = 0
    cen_to_piv = radius - hgt
    scale = cen_to_piv / (self.wid / 2)
    circ_cen_p = self.cent.lerp(rot_pos, scale)
    circ_cen_n = self.cent.lerp(rot_neg, scale)
    align_p0 = self.pts[0] - circ_cen_p
    align_p1 = self.pts[1] - circ_cen_p
    self.ang_meas = align_p0.angle(align_p1, 0.0)
    if self.ang_meas == 0.0:
        self.bad_input = True
        return
    else:
        self.bad_input = False

    if radius > self.wid/2 and hgt > radius:
        self.ang_meas = 2 * pi - self.ang_meas

    dist_sn_to_pos = (rot_pos - snap).length
    dist_sn_to_neg = (rot_neg - snap).length

    if dist_sn_to_pos > dist_sn_to_neg:  # closer to negative
        self.new_pts = self.pts[1], self.pts[0]
        self.circ_cen = circ_cen_p

    else:  # dist_sn_to_pos < dist_sn_to_neg / closer to positive
        self.new_pts = self.pts[0], self.pts[1]
        self.circ_cen = circ_cen_n


def click_handler(self, context):
    snap = self.snap.get_co(self.curr_ed_type)

    if self.paused:
        return

    elif self.stage == PLACE_1ST:
        add_pt(self, snap)
        self.prev_co = self.pts[-1].copy()
        self.stage += 1
        self.snap.grab(self.curr_ed_type)

    elif self.stage == PLACE_2ND:
        #draw_line
        if snap not in self.pts:
            add_pt(self, snap)
            self.stage += 1
            # move snap point to arch center before turning grab mode back on
            # as axis locks work from where an object was grabbed
            self.cent = self.pts[0].lerp(self.pts[1], 0.5)
            self.mov_aligned = self.pts[0] - self.cent
            self.wid = (self.pts[0] - self.pts[1]).length

            self.prev_co = self.cent.copy()
            self.snap.move(self.curr_ed_type, self.cent)
            cent2d = loc3d_to_reg2d(self.reg, self.rv3d, self.cent)
            warp_cursor(self, context, cent2d)
        self.snap.grab(self.curr_ed_type)

    elif self.stage == PLACE_3RD:
        # draw_arch
        if not self.bad_input:
            update_gui(self)
            add_pt(self, snap)
            self.stage += 1

            self.snap.move(self.curr_ed_type, self.circ_cen.copy())
            bpy.ops.object.editmode_toggle()
            self.curr_ed_type = context.mode
            inv_mw = self.snap.point.matrix_world.inverted()
            piv_cent = inv_mw @ self.circ_cen
            bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
            bm.verts.new(inv_mw @ self.new_pts[0])
            # Spin and deal with geometry on side 'a'
            edges_start_a = bm.edges[:]
            geom_start_a = bm.verts[:] + edges_start_a
            ret = bmesh.ops.spin(
                bm,
                geom=geom_start_a,
                angle=self.ang_meas,
                steps=self.segm_cnt,
                axis=self.piv_norm,
                cent=piv_cent)
            #edges_end_a = [ele for ele in ret["geom_last"]
            #        if isinstance(ele, bmesh.types.BMEdge)]
            del ret
            if not self.extr_enabled:
                self.stage = EXIT
            else:
                bpy.context.scene.cursor.location = self.circ_cen
                bpy.context.tool_settings.snap_target = 'ACTIVE'
                bpy.context.tool_settings.transform_pivot_point = 'CURSOR'
                bpy.context.scene.transform_orientation_slots[0].type = 'GLOBAL'
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.extrude_region_move()
                #bpy.ops.transform.resize('INVOKE_DEFAULT',
                #        constraint_orientation='GLOBAL')
                bpy.ops.transform.resize('INVOKE_DEFAULT',
                        orient_type='GLOBAL')

                self.stage = ARCH_EXTRUDE_1
        else:
            self.snap.grab(self.curr_ed_type)

    elif self.stage == ARCH_EXTRUDE_1:
        bpy.context.tool_settings.mesh_select_mode = False, False, True
        #bpy.context.scene.transform_orientation_slots[0].type = 'LOCAL'
        bpy.context.tool_settings.snap_target = 'CLOSEST'
        bpy.context.tool_settings.transform_pivot_point = 'MEDIAN_POINT'
        update_gui(self)
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.view3d.edit_mesh_extrude_move_normal('INVOKE_DEFAULT')
        self.stage = ARCH_EXTRUDE_2

    elif self.stage == ARCH_EXTRUDE_2:
        self.stage = EXIT

'''
# To-Do : make something nicer than this for handling GUI changes...
def update_gui2(self):
    self.gui_text_sett = {
        "title_txt_sz": 24,
        #bar_txt_sz": 18,  # tablet
        "bar_txt_sz": 12,
    }
    self.gui_text_sett.update(
        title_txt = "place 3 points to create arch",
        title_aln = "C",

        bar_t_txt_1 = "LMB - place point, CTRL - snap point, ",
        bar_t_txt_2 = "XYZ - add axis lock, C - clear axis lock",
        bar_t_aln_1 = "L",
    )
    self.helpdisp.clear_str()
    if not self.pause:
        if self.stage < ARCH_EXTRUDE_1:
            self.helpdisp.add_str(
                "INS",
                "place 3 points to create arch",
                title_txt_sz,
                'C')
            self.helpdisp.add_str(
                "TOP",
                "LMB - place point, CTRL - snap point, "
                "XYZ - add axis lock, C - clear axis lock",
                bar_txt_sz,
                'L')
'''

# To-Do : make something nicer than this for handling GUI changes...
def update_gui(self):
    title_txt_sz = 24
    bar_txt_sz = 12
    #bar_txt_sz = 18  # tablet
    self.helpdisp.clear_str()
    if not self.paused:
        if self.stage < ARCH_EXTRUDE_1:
            self.helpdisp.add_str(
                "INS",
                "place 3 points to create arch",
                title_txt_sz,
                'C')
            self.helpdisp.add_str(
                "TOP",
                "LMB - place point, CTRL - snap point, "
                "XYZ - add axis lock, C - clear axis lock",
                bar_txt_sz,
                'L')
            self.helpdisp.add_str(
                "BOT",
                "SPACE - pause to navigate / change settings",
                bar_txt_sz,
                'L')
            self.helpdisp.add_str(
                "BOT",
                "ESC, RMB - quit",
                bar_txt_sz,
                'R')
        elif self.stage == ARCH_EXTRUDE_1:
            self.helpdisp.add_str(
                "INS",
                "set arch width / thickness",
                title_txt_sz,
                'C')
            self.helpdisp.add_str(
                "TOP",
                "LMB - confirm width",
                bar_txt_sz,
                'L')
            self.helpdisp.add_str(
                "BOT",
                "",
                bar_txt_sz,
                'L')
            self.helpdisp.add_str(
                "BOT",
                "ESC, RMB - quit",
                bar_txt_sz,
                'R')
        else:
            self.helpdisp.add_str(
                "INS",
                "set arch length",
                title_txt_sz,
                'C')
            self.helpdisp.add_str(
                "TOP",
                "LMB - confirm length, CTRL - snap point",
                bar_txt_sz,
                'L')
            self.helpdisp.add_str(
                "BOT",
                "",
                bar_txt_sz,
                'L')
            self.helpdisp.add_str(
                "BOT",
                "ESC, RMB - quit",
                bar_txt_sz,
                'R')
    else:
        if self.stage < ARCH_EXTRUDE_1:
            self.helpdisp.add_str(
                "INS",
                "paused, navigate or change settings",
                title_txt_sz,
                'C')
            self.helpdisp.add_str(
                "TOP",
                "UP / MSWH_UP - increase segments, "
                "DOWN / MSWH_DOWN - decrease segments",
                bar_txt_sz,
                'L')
            self.helpdisp.add_str(
                "BOT",
                "SPACE - resume point placement, R - reset point placement",
                bar_txt_sz,
                'L')
            self.helpdisp.add_str(
                "BOT",
                "ESC, RMB - quit",
                bar_txt_sz,
                'R')
        else:
            self.helpdisp.add_str(
                "INS",
                "paused, navigate to better position",
                title_txt_sz,
                'C')
            self.helpdisp.add_str(
                "TOP",
                "",
                bar_txt_sz,
                'L')
            self.helpdisp.add_str(
                "BOT",
                "SPACE - resume extrude",
                bar_txt_sz,
                'L')
            self.helpdisp.add_str(
                "BOT",
                "ESC, RMB - quit",
                bar_txt_sz,
                'R')
    self.helpdisp.update()


def retreive_settings(arg):
    settings_dict = {}
    if arg == "csc_default_grey":
        settings_dict.update(
            col_font_np = (0.95, 0.95, 0.95, 1.0),
            col_font_instruct_main = (0.67, 0.67, 0.67, 1.0),
            col_font_instruct_shadow = (0.15, 0.15, 0.15, 1.0),
            col_font_keys = (0.15, 0.15, 0.15, 1.0),
            col_field_keys_aff = (0.51, 0.51, 0.51, 1.0),
            col_field_keys_neg = (0.41, 0.41, 0.41, 1.0),

            col_line_main = (0.9, 0.9, 0.9, 1.0),
            col_line_shadow = (0.1, 0.1, 0.1, 0.25),
            col_num_main = (0.95, 0.95, 0.95, 1.0),
            col_num_shadow = (0.0, 0.0, 0.0, 0.75),

            col_gw_line_cross = (0.25, 0.35, 0.4, 0.87),
            col_gw_line_base_free = (1.0, 1.0, 1.0, 0.85),
            col_gw_line_base_lock_x = (1.0, 0.0, 0.0, 1.0),
            col_gw_line_base_lock_y = (0.5, 0.75, 0.0, 1.0),
            col_gw_line_base_lock_z = (0.0, 0.2, 0.85, 1.0),
            col_gw_line_base_lock_arb = (0.0, 0.0, 0.0, 0.5),
            col_gw_line_all = (1.0, 1.0, 1.0, 0.85),

            col_gw_fill_base_x = (1.0, 0.0, 0.0, 0.2),
            col_gw_fill_base_y = (0.0, 1.0, 0.0, 0.2),
            col_gw_fill_base_z = (0.0, 0.2, 0.85, 0.2),
            col_gw_fill_base_arb = (0.0, 0.0, 0.0, 0.15),

            col_bg_fill_main_run = (1.0, 0.5, 0.0, 1.0),
            col_bg_fill_main_nav = (0.5, 0.75 ,0.0 ,1.0),
            col_bg_fill_square = (0.0, 0.0, 0.0, 1.0),
            col_bg_fill_aux = (0.4, 0.15, 0.75, 1.0),

            col_bg_line_symbol = (1.0, 1.0, 1.0, 1.0),
            col_bg_font_main = (1.0, 1.0, 1.0, 1.0),
            col_bg_font_aux = (1.0, 1.0, 1.0, 1.0)
        )
    elif arg == "csc_school_marine":
        settings_dict.update(
            col_font_np = (0.25, 0.35, 0.4, 0.87),
            col_font_instruct_main = (1.0, 1.0, 1.0, 1.0),
            col_font_instruct_shadow = (0.25, 0.35, 0.4, 0.6),
            col_font_keys = (1.0, 1.0, 1.0, 1.0),
            col_field_keys_aff = (0.55, 0.6, 0.64, 1.0),
            col_field_keys_neg = (0.67, 0.72, 0.76, 1.0),

            col_line_main = (1.0, 1.0, 1.0, 1.0),
            col_line_shadow = (0.1, 0.1, 0.1, 1.0),
            #col_line_shadow = (0.1, 0.1, 0.1, 0.25),
            col_num_main = (0.25, 0.35, 0.4, 1.0), #(1.0, 0.5, 0.0, 1.0)
            col_num_shadow = (1.0, 1.0, 1.0, 1.0),

            col_gw_line_cross = (0.25, 0.35, 0.4, 0.87),
            col_gw_line_base_free = (1.0, 1.0, 1.0, 0.85),
            col_gw_line_base_lock_x = (1.0, 0.0, 0.0, 1.0),
            col_gw_line_base_lock_y = (0.5, 0.75, 0.0, 1.0),
            col_gw_line_base_lock_z = (0.0, 0.2, 0.85, 1.0),
            col_gw_line_base_lock_arb = (0.0, 0.0, 0.0, 0.5),
            col_gw_line_all = (1.0, 1.0, 1.0, 0.85),

            col_gw_fill_base_x = (1.0, 0.0, 0.0, 0.2),
            col_gw_fill_base_y = (0.0, 1.0, 0.0, 0.2),
            col_gw_fill_base_z = (0.0, 0.2, 0.85, 0.2),
            col_gw_fill_base_arb = (0.0, 0.0, 0.0, 0.15),

            col_bg_fill_main_run = (1.0, 0.5, 0.0, 1.0),
            col_bg_fill_main_nav = (0.5, 0.75 ,0.0 ,1.0),
            col_bg_fill_square = (0.0, 0.0, 0.0, 1.0),
            col_bg_fill_aux = (0.4, 0.15, 0.75, 1.0),
                    #(0.4, 0.15, 0.75, 1.0) (0.2, 0.15, 0.55, 1.0) ?
            col_bg_line_symbol = (1.0, 1.0, 1.0, 1.0),
            col_bg_font_main = (1.0, 1.0, 1.0, 1.0),
            col_bg_font_aux = (1.0, 1.0, 1.0, 1.0)
        )
    elif arg == "def_blender_gray":
        settings_dict.update(
            # commented == not changed from csc_school_marine
            #col_font_np = (0.25, 0.35, 0.4, 0.87),  # logo
            col_font_instruct_main = (0.9, 0.9, 0.9, 1.0),
            col_font_instruct_shadow = (0.3, 0.3, 0.3, 1.0),
            col_font_keys = (0.01, 0.01, 0.01, 1.0),
            col_field_keys_aff = (0.55, 0.55, 0.55, 1.0),
            col_field_keys_neg = (0.36, 0.36, 0.36, 1.0),

            col_line_main = (95.0, 95.0, 95.0, 1.0),
            #col_line_shadow = (0.1, 0.1, 0.1, 1.0),
            col_num_main = (0.85, 0.85, 0.85, 1.0),
            col_num_shadow = (0.2, 0.2, 0.2, 1.0),

            #col_gw_line_cross = (0.25, 0.35, 0.4, 0.87),
            #col_gw_line_base_free = (1.0, 1.0, 1.0, 0.85),
            #col_gw_line_base_lock_x = (1.0, 0.0, 0.0, 1.0),
            #col_gw_line_base_lock_y = (0.5, 0.75, 0.0, 1.0),
            #col_gw_line_base_lock_z = (0.0, 0.2, 0.85, 1.0),
            #col_gw_line_base_lock_arb = (0.0, 0.0, 0.0, 0.5),
            #col_gw_line_all = (1.0, 1.0, 1.0, 0.85),

            #col_gw_fill_base_x = (1.0, 0.0, 0.0, 0.2),
            #col_gw_fill_base_y = (0.0, 1.0, 0.0, 0.2),
            #col_gw_fill_base_z = (0.0, 0.2, 0.85, 0.2),
            #col_gw_fill_base_arb = (0.0, 0.0, 0.0, 0.15),

            #col_bg_fill_main_run = (1.0, 0.5, 0.0, 1.0),
            #col_bg_fill_main_nav = (0.5, 0.75 ,0.0 ,1.0),
            #col_bg_fill_square = (0.0, 0.0, 0.0, 1.0),
            #col_bg_fill_aux = (0.4, 0.15, 0.75, 1.0),

            #col_bg_line_symbol = (1.0, 1.0, 1.0, 1.0),
            #col_bg_font_main = (1.0, 1.0, 1.0, 1.0),
            #col_bg_font_aux = (1.0, 1.0, 1.0, 1.0)
        )
    return settings_dict


def draw_callback_px(self, context):
    reg = bpy.context.region
    rv3d = bpy.context.region_data
    snap = self.snap.get_co(self.curr_ed_type)
    pts2d = []
    line_pts = []

    if self.stage == PLACE_1ST:
        #guide2d = loc3d_to_reg2d(reg, rv3d, snap)
        guide2d = safe_loc3d_to_reg2d(reg, rv3d, [snap])

    elif self.stage == PLACE_2ND:
        #guide2d = loc3d_to_reg2d(reg, rv3d, snap)
        guide2d = safe_loc3d_to_reg2d(reg, rv3d, [snap])
        #pts2d = [loc3d_to_reg2d(reg, rv3d, i) for i in self.pts]
        pts2d = safe_loc3d_to_reg2d(reg, rv3d, self.pts)
        line_pts = self.pts[0], snap
        #if not len(pts2d) > 0:
        #    print("len(pts2d) == 0")

    elif self.stage == PLACE_3RD:
        #guide2d = loc3d_to_reg2d(reg, rv3d, snap)
        guide2d = safe_loc3d_to_reg2d(reg, rv3d, [snap])
        #pts2d = [loc3d_to_reg2d(reg, rv3d, i) for i in self.pts]
        pts2d = safe_loc3d_to_reg2d(reg, rv3d, self.pts)

        # attempt to draw arch
        update_arch(self, snap)
        if not self.bad_input and self.circ_cen is not None:
            arch_top = get_rotated_pt(self.circ_cen, self.new_pts[0],
                    self.ang_meas/2, self.piv_norm)
            line_pts = self.cent, arch_top

            draw_circ_arch_3D(self.segm_cnt, self.new_pts, self.circ_cen,
                    self.ang_meas, self.piv_norm, Colr.green, reg, rv3d)
        else:
            if len(pts2d) > 1:
                draw_line_2D(pts2d[0], pts2d[1], Colr.white)

    elif self.stage == ARCH_EXTRUDE_1:
        bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
        if hasattr(bm.verts, "ensure_lookup_table"):
            bm.verts.ensure_lookup_table()
        vts = bm.verts
        vert_cnt = self.segm_cnt + 1
        v_cent1_idx = vert_cnt // 2
        v_cent2_idx = v_cent1_idx + vert_cnt
        m_w = bpy.context.edit_object.matrix_world
        v1 = m_w @ vts[v_cent1_idx].co
        v2 = m_w @ vts[v_cent2_idx].co
        line_pts = v1, v2

        pts_cust = self.pts[0], self.pts[1], v1
        #pts2d = [loc3d_to_reg2d(reg, rv3d, i) for i in pts_cust]
        pts2d = safe_loc3d_to_reg2d(reg, rv3d, pts_cust)
        #guide2d = loc3d_to_reg2d(reg, rv3d, v2)
        guide2d = safe_loc3d_to_reg2d(reg, rv3d, [v2])

    elif self.stage == ARCH_EXTRUDE_2:
        bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
        if hasattr(bm.verts, "ensure_lookup_table"):
            bm.verts.ensure_lookup_table()
        vts = bm.verts
        vert_cnt = self.segm_cnt + 1
        v_cent1_idx = vert_cnt // 2
        v_cent2_idx = v_cent1_idx + (vert_cnt * 2)
        m_w = bpy.context.edit_object.matrix_world
        v1 = m_w @ vts[v_cent1_idx].co
        v2 = m_w @ vts[v_cent2_idx].co
        line_pts = v1, v2
        #pts2d = [loc3d_to_reg2d(reg, rv3d, v1)]
        pts2d = safe_loc3d_to_reg2d(reg, rv3d, [v1])
        #guide2d = loc3d_to_reg2d(reg, rv3d, v2)
        guide2d = safe_loc3d_to_reg2d(reg, rv3d, [v2])

    if line_pts != []:
        self.mean_dist.draw(line_pts, self.meas_mult, self.meas_suff)
    #if len(pts2d) > 0:
    #    draw_pt_2D(pts2d, Colr.white)
    draw_pt_2D(pts2d, Colr.white)
    #for i in pts2d:
    #    draw_pt_2D(i, Colr.white)
    #print("draw_pt_2D(guide2d, Colr.green)", guide2d)
    draw_pt_2D(guide2d, Colr.green)

    # display number of segments
    if self.paused and self.stage < ARCH_EXTRUDE_1:
        if guide2d is not None:
            self.segm_cntr.draw(self.segm_cnt, guide2d[0])
            #self.segm_cntr.draw(self.segm_cnt, self.mouse_loc)

    self.helpdisp.draw()


# To-Do : move to DrawSegmCounter?
def segm_decrm(self):
    if self.segm_cnt > 2:
        self.segm_cnt -= 1


class TPARCH_OT_modal(bpy.types.Operator):
    '''Launch the arch tool'''
    bl_idname = "view3d.modal_arch_tool"
    bl_label = "Three Point Arch Tool"

    # Only launch Add-On from OBJECT or EDIT modes
    @classmethod
    def poll(self, context):
        return context.mode == 'OBJECT' or context.mode == 'EDIT_MESH'

    def modal(self, context, event):
        context.area.tag_redraw()
        self.curr_ed_type = context.mode

        if event.type in {'MIDDLEMOUSE', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3',
        'NUMPAD_4', 'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8', 'NUMPAD_9', 'NUMPAD_5'}:
            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            self.mouse_loc = Vector((event.mouse_region_x, event.mouse_region_y))

        if event.type in {'RET', 'LEFTMOUSE'} and event.value == 'RELEASE':
            click_handler(self, context)

        if event.type == 'SPACE' and event.value == 'RELEASE':
            if not self.paused:
                self.paused = True
                update_gui(self)
            else:
                self.paused = False
                update_gui(self)
                if self.stage < ARCH_EXTRUDE_1:
                    self.snap.grab(self.curr_ed_type)
                elif self.stage == ARCH_EXTRUDE_1:
                    bpy.ops.transform.resize('INVOKE_DEFAULT',
                            orient_type='GLOBAL')
                else:
                    bpy.ops.transform.translate('INVOKE_DEFAULT',
                            constraint_axis=(False, False, True),
                            orient_type='NORMAL',
                            release_confirm=True)

        if self.paused:
            if self.stage < ARCH_EXTRUDE_1:
                if event.type == 'WHEELUPMOUSE':
                    self.segm_cnt += 1

                if event.type == 'WHEELDOWNMOUSE':
                    segm_decrm(self)

                if event.type == 'UP_ARROW' and event.value == 'RELEASE':
                    self.segm_cnt += 1

                if event.type == 'DOWN_ARROW' and event.value == 'RELEASE':
                    segm_decrm(self)

            if event.type == 'R' and event.value == 'RELEASE':
                self.paused = False
                update_gui(self)
                if self.prev_co is not None:
                    last2d = loc3d_to_reg2d(self.reg, self.rv3d, self.prev_co)
                    self.snap.move(self.curr_ed_type, self.prev_co)
                    warp_cursor(self, context, last2d)
                    self.snap.grab(self.curr_ed_type)
                else:
                    self.snap.mouse_grab(self.mouse_loc, self.curr_ed_type)

        # start debug console
        '''
        if event.type == 'D' and event.value == 'RELEASE':
            __import__('code').interact(local=dict(globals(), **locals()))

        if self.force_quit:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            exit_addon(self)
            return {'CANCELLED'}
        '''
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'RELEASE':
            #print("pressed ESC or RIGHTMOUSE")  # debug
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self.force_quit = True
            exit_addon(self)
            return {'CANCELLED'}

        if self.stage == EXIT:
            if self.curr_ed_type == 'EDIT_MESH':
                # recalc normals outside just in case they were inverted
                bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
                bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            exit_addon(self)
            return {'FINISHED'}

        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        if context.area.type == 'VIEW_3D':
            args = (self, context)

            # Add the region OpenGL drawing callback
            # draw in view space with 'POST_VIEW' and 'PRE_VIEW'
            self._handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_px,
                    args, 'WINDOW', 'POST_PIXEL')

            if context.mode == 'EDIT_MESH':
                bpy.ops.object.editmode_toggle()
            bpy.ops.object.select_all(action='DESELECT')

            addon_prefs = context.preferences.addons[__name__].preferences
            #sett_dict = retreive_settings(addon_prefs.np_col_scheme)
            sett_dict = retreive_settings("def_blender_gray")

            self.helpdisp = HelpDisplay(context.region, sett_dict)
            self.mean_dist = DrawMeanDistance(18, sett_dict)
            self.segm_cntr = DrawSegmCounter(sett_dict)
            self.curr_ed_type = context.mode  # current Blender Editor Type
            self.stage = PLACE_1ST
            self.mouse_loc = Vector((event.mouse_region_x, event.mouse_region_y))
            self.reg = bpy.context.region
            self.rv3d = bpy.context.region_data
            self.piv_norm = None
            self.segm_cnt = addon_prefs.segm_cnt  # move to DrawSegmCounter?
            self.meas_mult = addon_prefs.np_scale_dist
            self.meas_suff = ''
            self.pt_cnt = 0
            self.pts = []
            self.new_pts = None
            self.prev_co = None  # previous coordinate
            self.cent = None
            self.ang_meas = None
            self.circ_cen = None
            self.wid = None
            self.rad90 = radians(90)
            self.mov_aligned = None
            self.snap = SnapPoint()
            self.settings_backup = backup_blender_settings()
            self.sel_backup = None  # place holder
            self.bad_input = False
            self.extr_enabled = addon_prefs.extr_enabled
            #self.debug_flag = False
            self.paused = False
            self.force_quit = False

            tmp_suff = addon_prefs.np_suffix_dist
            if tmp_suff != 'None':
                self.meas_suff = tmp_suff

            context.window_manager.modal_handler_add(self)

            init_blender_settings()
            update_gui(self)
            self.snap.create(self.mouse_loc, self.curr_ed_type)
            #print("Add-on started!")  # debug

            return {'RUNNING_MODAL'}
        else:
            self.report({'WARNING'}, "View3D not found, cannot run operator")
            return {'CANCELLED'}


class TPARCH_PT_panel(bpy.types.Panel):
    # Creates a panel in the 3d view Toolshelf window
    bl_label = 'Arch Panel'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = 'objectmode'
    bl_category = 'Create'

    def draw(self, context):
        #layout = self.layout
        row = self.layout.row(align=True)
        row.operator("view3d.modal_arch_tool", text="Create Arch", icon="SPHERECURVE")


def register():
    bpy.utils.register_class(TPARCH_prefs)
    bpy.utils.register_class(TPARCH_OT_modal)
    bpy.utils.register_class(TPARCH_PT_panel)

def unregister():
    bpy.utils.unregister_class(TPARCH_PT_panel)
    bpy.utils.unregister_class(TPARCH_OT_modal)
    bpy.utils.unregister_class(TPARCH_prefs)

if __name__ == "__main__":
    register()
