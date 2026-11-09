import streamlit as st
import ezdxf
import math
import re
import tempfile
import subprocess
import os
import gc
from pathlib import Path

st.set_page_config(page_title="NEC Placer", layout="wide")
st.markdown("""
<style>
    #MainMenu, footer, header {visibility: hidden;}
    .stDeployButton {display:none;}
    section[data-testid="stSidebar"] {display:none;}
    .block-container {padding: 0 !important; max-width: 100% !important;}
    .credit {position: fixed; right: 18px; bottom: 12px; font-size: 12px;
             opacity: 0.55; z-index: 1000; pointer-events: none;}
</style>
<div class="credit">Alejandro M. C.</div>
""", unsafe_allow_html=True)

def dwg_to_dxf(dwg_path: Path) -> Path:
    try:
        out = dwg_path.with_suffix(".dxf")
        subprocess.run(["dwg2dxf", "-y", "-o", str(out), str(dwg_path)], capture_output=True, timeout=240)
        if not (out.exists() and out.stat().st_size>0):
            subprocess.run(["dwg2dxf", "-y", "--as", "r2000", "-o", str(out), str(dwg_path)], capture_output=True, timeout=240)
        if out.exists() and out.stat().st_size>0:
            try: fix_dxf_text(str(out))
            except Exception: pass
            return out
    except Exception: pass
    return None

def dxf_to_dwg(dxf_path: Path) -> Path:
    """libredwg dxf2dwg — writes R2000 reliably."""
    try:
        out = dxf_path.with_suffix(".dwg")
        subprocess.run(["dxf2dwg", "-y", "--as", "r2000", "-o", str(out), str(dxf_path)],
                       capture_output=True, timeout=120)
        if out.exists() and out.stat().st_size > 0:
            return out
    except: pass
    return None

SKIP = ["EXIST-SPOT-ELEV","Surface_CONTOUR","Surface_CONTOUR_TAG",
        "Surface_CONTOUR_IDX","Surface_CONTOUR_MID","SECTTAG",
        "site-info","SECCION-LINE","PROPERTY LIMT","_NATURAL",
        "north","SITE","TABLE","TABLEDATA","TABLELN"]

SKIP_BLOCKS = {"*","AME_NIL","AME_SOL","FLECHA-X","2-TIT360",
               "ELE1","ELE2","ELE3","ELE4","SECT-1","SECT-2",
               "SECT-3","SECT-4","AVE_RENDER"}

def clean_mtext(txt):
    txt = re.sub(r'\\[LlOoKk]','',txt)          # underline / overline / strike toggles
    txt = re.sub(r'\\f[^;]+;','',txt)
    txt = re.sub(r'\\[A-Za-z][^;]*;','',txt)
    txt = re.sub(r'\{|\}','',txt)
    txt = re.sub(r'\\U\+[0-9A-Fa-f]+','',txt)
    return txt.replace('\\P',' ').replace('\\~',' ').strip()

# ── Output layer scheme (AIA-style, colors BYLAYER) ────────────────────────
OUT_LAYERS = {
    # name          (color, linetype,     lineweight)
    "A-WALL":       (8,  "Continuous", 35),
    "A-DOOR":       (9,  "Continuous", 18),
    "A-GLAZ":       (9,  "Continuous", 18),
    "A-COLS":       (8,  "Continuous", 25),
    "A-STAIR":      (9,  "Continuous", 18),
    "A-FURN":       (9,  "Continuous", 13),
    "A-FLOR":       (9,  "Continuous", 13),
    "A-PATT":       (9,  "Continuous", 9),
    "A-GENM":       (9,  "Continuous", 13),
    "A-CLNG":       (9,  "Continuous", 13),
    "S-GRID":       (8,  "CENTER",     13),
    "S-GRID-IDEN":  (8,  "Continuous", 13),
    "A-ANNO-TEXT":  (8,  "Continuous", 13),
    "A-AREA-IDEN":  (8,  "Continuous", 13),
    "A-RCP":        (9,  "Continuous", 13),
    "E-ANNO-TITL":  (7,  "Continuous", 35),
    "E-ANNO-TTLB":  (7,  "Continuous", 35),
    "E-ANNO-TEXT":  (7,  "Continuous", 18),
    "E-SYMB":       (7,  "Continuous", 25),
    "E-POWR-DEVC":  (7,  "Continuous", 35),
    "E-POWR-CIRC":  (2,  "Continuous", 25),
    "E-LITE-FIXT":  (7,  "Continuous", 35),
    "E-LITE-CIRC":  (2,  "Continuous", 25),
    "E-COMM-DEVC":  (7,  "Continuous", 35),
    "E-COMM-CIRC":  (2,  "Continuous", 25),
    "E-PANL":       (7,  "Continuous", 50),
    "DEFPOINTS":    (7,  "Continuous", 0),
}

def is_grid_layer(name):
    u = name.upper()
    return "GRID" in u or u in ("EJE","EJES","A-EJES","EJES-ARQ")

def map_layer(src, et="LINE"):
    """Source layer → consolidated output layer."""
    u = src.upper()
    if is_grid_layer(u):
        return "S-GRID" if et in ("LINE","LWPOLYLINE","SPLINE") else "S-GRID-IDEN"
    if "PATT" in u or "HATCH" in u:                       return "A-PATT"
    if any(k in u for k in ("WALL","MURO","PARED")):      return "A-WALL"
    if any(k in u for k in ("DOOR","PUERTA")):            return "A-DOOR"
    if any(k in u for k in ("WINDOW","VENTANA","GLAZ","CURT")): return "A-GLAZ"
    if any(k in u for k in ("COLS","COLUMN","COLUMNA")):  return "A-COLS"
    if any(k in u for k in ("STAIR","ESCAL","RAIL")):     return "A-STAIR"
    if any(k in u for k in ("FURN","MUEBLE","CASEWORK","MLWK","CABINET",
                            "EQPM","PLUMB","FIXT","SANIT")): return "A-FURN"
    if any(k in u for k in ("FLOR","FLOOR","PISO")):      return "A-FLOR"
    if any(k in u for k in ("CLNG","CEIL","LITE","LIGHT")): return "A-CLNG"
    return "A-GENM"

def text_layer(src):
    return "S-GRID-IDEN" if is_grid_layer(src) else "A-ANNO-TEXT"

_GRID_ID=re.compile(r"^([A-Z]{1,2}|\d{1,3}(\.\d)?|[A-Z]\.?\d{1,2}|\d{1,2}[A-Z]|[A-Z]{1,2}-?\d{1,2})$")
# Whole-word note-speak and construction-material callouts → not labels
_NOTE_WORDS={"SEE","TYP","TYPICAL","NOTE","NOTES","REF","SIM","UNO","MIN","MAX","PROVIDE",
    "VERIFY","MATCH","ALIGN","SCALE","NTS","DETAIL","SECTION","CLR","OC","AFF","CONTRACTOR",
    "SHALL","INSTALL","FURNISH","COORDINATE","APPROX","EQ","EQUAL","DRAWING","DWG","SHEET",
    "REVISION","REV","DRAWN","CHECKED","DIM","DIMS","DIMENSION","ELEVATION","FFE","TOS","BOS",
    "GYP","GWB","CONC","CMU","STUD","PLYWD","PLYWOOD","SLAB","FTG","FOOTING","JOIST",
    "SHEATHING","INSUL","INSULATION","CAULK","SEALANT","FLASHING","PAINT","FINISH","VCT",
    "CARPET","GRANITE","SOFFIT","PARAPET","CURB","SLOPE","DN","UP","RO","ROUGH","OPENING",
    "HDR","HEADER","SILL","LINTEL","NORTH","NORTE","PROPERTY","PROPIEDAD","LIMIT","LIMITE",
    "LOT","LOTE","SETBACK","EASEMENT","SCHEDULE","LEGEND","KEY","GYPSUM","FASCIA","ABOVE","BELOW",
    "TRANSLUCENT","SLIDING","TEMPERED","OVERHEAD","BEYOND","EXISTING","EXIST","DEMO","DEMOLISH"}
_ROOMLIKE=re.compile(r"^[A-ZÁÉÍÓÚÑ&/.\- ]+( ?[A-Z]{0,3}-?\d{1,3}[A-Z]?)?$")
def is_grid_id(txt):
    return bool(_GRID_ID.match(txt.strip().upper()))
def is_room_label(txt):
    """Keep text that reads like a room / area / equipment label
    (KITCHEN, TIRE CENTER, EV CHARGER, MTL COLUMN, PANEL LP-1, BAY 3)."""
    s=txt.strip().upper()
    if not (2<=len(s)<=40): return False
    if any(ch in s for ch in '=@#%$<>{}[]|\\"°'): return False
    if "'" in s or '"' in s: return False           # feet / inch marks
    if len(s.split())>4: return False
    words=set(re.findall(r"[A-Z]+",s))
    if words & _NOTE_WORDS: return False
    letters=sum(ch.isalpha() for ch in s)
    if letters/len(s)<0.6: return False
    return bool(_ROOMLIKE.fullmatch(s))

def leaf_geom(ve, flat):
    """WCS geometry of a leaf entity: handles OCS / mirrored extrusions.
    Returns ('line',p1,p2) | ('poly',pts,closed) | ('circle',c,r) | ('arc',c,r,s,e) | None."""
    t=ve.dxftype()
    if t=="LINE":
        return ("line",(ve.dxf.start.x,ve.dxf.start.y),(ve.dxf.end.x,ve.dxf.end.y))
    if t=="LWPOLYLINE":
        pts=[(v.x,v.y) for v in ve.vertices_in_wcs()]
        return ("poly",pts,ve.is_closed) if len(pts)>1 else None
    if t=="POLYLINE" and ve.is_2d_polyline:
        ocs=ve.ocs(); pts=[]
        for p in ve.points():
            w=ocs.to_wcs(p); pts.append((w.x,w.y))
        return ("poly",pts,ve.is_closed) if len(pts)>1 else None
    if t=="CIRCLE":
        cw=ve.ocs().to_wcs(ve.dxf.center); return ("circle",(cw.x,cw.y),ve.dxf.radius)
    if t=="ARC":
        cw=ve.ocs().to_wcs(ve.dxf.center); s=ve.start_point; e=ve.end_point
        s=(s.x,s.y); e=(e.x,e.y)
        if ve.dxf.extrusion.z<0: s,e=e,s          # OCS flip reverses direction
        return ("arc",(cw.x,cw.y),ve.dxf.radius,s,e)
    if t in ("ELLIPSE","SPLINE"):
        try: pts=[(p.x,p.y) for p in ve.flattening(flat)]
        except: return None
        return ("poly",pts,False) if len(pts)>1 else None
    return None

def geom_mid(g):
    k=g[0]
    if k=="line": return ((g[1][0]+g[2][0])/2,(g[1][1]+g[2][1])/2)
    if k=="poly": return (sum(p[0] for p in g[1])/len(g[1]),sum(p[1] for p in g[1])/len(g[1]))
    return g[1]

def geom_bbox(g):
    k=g[0]
    if k=="line": xs=(g[1][0],g[2][0]); ys=(g[1][1],g[2][1])
    elif k=="poly": xs=[p[0] for p in g[1]]; ys=[p[1] for p in g[1]]
    else: (cx,cy),r=g[1],g[2]; xs=(cx-r,cx+r); ys=(cy-r,cy+r)
    return min(xs),max(xs),min(ys),max(ys)

def write_leaf(out_msp,g,attrs,PT=None,reflect=False):
    PT=PT or (lambda x,y:(x,y))
    k=g[0]
    if k=="line":
        out_msp.add_line(PT(*g[1]),PT(*g[2]),dxfattribs=attrs)
    elif k=="poly":
        out_msp.add_lwpolyline([PT(*p) for p in g[1]],dxfattribs={**attrs,"closed":g[2]})
    elif k=="circle":
        out_msp.add_circle(center=PT(*g[1]),radius=g[2],dxfattribs=attrs)
    elif k=="arc":
        c=PT(*g[1]); s=PT(*g[3]); e=PT(*g[4])
        sa=math.degrees(math.atan2(s[1]-c[1],s[0]-c[0]))%360
        ea=math.degrees(math.atan2(e[1]-c[1],e[0]-c[0]))%360
        if reflect: sa,ea=ea,sa
        out_msp.add_arc(center=c,radius=g[2],start_angle=sa,end_angle=ea,dxfattribs=attrs)

def iter_leaves(ents, doc, max_depth, ceil_fn=None, skip=(), keep_insert=None):
    """Yield (leaf_entity, in_ceil) walking INSERTs with ezdxf's own resolver,
    so base points, mirrored inserts, non-uniform scale and OCS are all right."""
    def rec(es, d, in_ceil):
        for e in es:
            try:
                lay=getattr(e.dxf,'layer','0')
                if lay in skip: continue
                if e.dxftype()=="INSERT":
                    if keep_insert and keep_insert(e):
                        yield e,in_ceil; continue          # keep as a block (a fixture), don't explode
                    if d>=max_depth or e.dxf.name not in doc.blocks: continue
                    nxt=in_ceil or (ceil_fn(lay) if ceil_fn else False)
                    try: sub=e.virtual_entities()
                    except Exception: continue
                    yield from rec(sub,d+1,nxt)
                    continue
                yield e,in_ceil
            except Exception:
                continue
    yield from rec(ents,0,False)

def sheet_score(name):
    n = name.lower()
    if any(k in n for k in ["floor plan","ground","a101","a-1"]): return 2
    if any(k in n for k in ["site","rcp","roof","ceiling","reflected"]): return 0
    return 1

_NON_FLOOR_LAYOUT_KW={"ROOF","ELEV","ELEVATION","ELEVATIONS","SECT","SECTION","SECTIONS",
    "AREA","SITE","RCP","REFLECT","REFLECTED","CEILING","DETAIL","DETAILS","SCHEDULE",
    "SCHEDULES","LEGEND","NOTES","TITLE","COVER","INDEX","DEMO","DEMOLITION","ST","GEN",
    "STRUCT","STRUCTURAL","ELECTRICAL","PLUMBING","MECH","MECHANICAL","LANDSCAPE","IRRIGATION"}
def _is_non_floor_layout(name):
    toks=re.split(r"[^A-Za-z0-9]+", name.upper())
    return any(t in _NON_FLOOR_LAYOUT_KW for t in toks if t)

def is_rcp(name):
    n = re.sub(r'[_\-]+',' ',name.lower())
    n = re.sub(r'\s+',' ',n)
    return any(k in n for k in ["rcp","reflect","ceiling","plafon","cielo"])

CEIL_LAYER_KW=("CLNG","CEIL","LITE","LIGHT","LAMP","LUM","PLAF","CIELO",
               "LUZ","LUCES","ILUMIN")
CEIL_BLOCK_KW=("LIGHT","LITE","LAMP","LUM","FIXT","DOWNL","RECESS","TROFFER",
               "LUZ","ILUMIN")
RCP_TEXT_KW=("REFLECTED","CEILING PLAN","RCP","PLAFON","PLAFÓN","CIELO RASO")

def _norm(s): return re.sub(r"[\s_\-\.]+","",str(s).lower())
def quick_scan(p):
    """Cheap per-file scan. Returns (has_vp, walls, xref, ceil, total, rcp_txt)."""
    try:
        doc = ezdxf.readfile(str(p))
        has_vp = any(e.dxftype()=="VIEWPORT"
                     for layout in doc.layouts if layout.name!="Model"
                     for e in layout)
        walls = sum(1 for e in doc.modelspace()
                    if e.dxftype()=="LINE" and
                    getattr(e.dxf,'layer','') in
                    ["AP-WALL","AR-WALLS","A-WALL","WALL","A-WALL-FULL","Walls"])
        xref = next((e.dxf.name for e in doc.modelspace()
                     if e.dxftype()=="INSERT" and e.dxf.name not in SKIP_BLOCKS), None)
        # Ceiling evidence: entities on ceiling/lighting layers or light blocks,
        # counted across modelspace + block definitions.
        ceil=0; total=0
        def tally(ent):
            nonlocal ceil,total
            try:
                total+=1
                lay=getattr(ent.dxf,'layer','').upper()
                if any(k in lay for k in CEIL_LAYER_KW): ceil+=1
                elif ent.dxftype()=="INSERT" and \
                     any(k in ent.dxf.name.upper() for k in CEIL_BLOCK_KW): ceil+=1
            except: pass
        for e in doc.modelspace(): tally(e)
        for blk in doc.blocks:
            if blk.name.startswith("*"): continue
            for e in blk: tally(e)
        # Sheet title / notes mentioning a ceiling plan (any layout)
        rcp_txt=False
        for layout in doc.layouts:
            for e in layout:
                try:
                    if e.dxftype()=="TEXT": s=e.dxf.text
                    elif e.dxftype()=="MTEXT": s=e.text
                    else: continue
                    if any(k in s.upper() for k in RCP_TEXT_KW):
                        rcp_txt=True; break
                except: pass
            if rcp_txt: break
        del doc
        return has_vp, walls, xref, ceil, total, rcp_txt
    except:
        return False, 0, None, 0, 0, False

# ── Standard scales ─────────────────────────────────────────────────────────
IMP_SCALES=[(12,'1"=1\'-0"'),(16,'3/4"=1\'-0"'),(24,'1/2"=1\'-0"'),(32,'3/8"=1\'-0"'),
            (48,'1/4"=1\'-0"'),(64,'3/16"=1\'-0"'),(96,'1/8"=1\'-0"'),(128,'3/32"=1\'-0"'),
            (192,'1/16"=1\'-0"'),(384,'1/32"=1\'-0"'),(768,'1/64"=1\'-0"')]
MET_SCALES=[(20,'1:20'),(25,'1:25'),(50,'1:50'),(75,'1:75'),(100,'1:100'),(125,'1:125'),
            (150,'1:150'),(200,'1:200'),(250,'1:250'),(500,'1:500'),(1000,'1:1000')]
UNIT_PER_IN={1:1.0, 2:12.0, 4:25.4, 5:2.54, 6:0.0254}   # $INSUNITS → model units per inch

def pick_scale(need, ins_units):
    metric = ins_units in (4,5,6)
    table = MET_SCALES if metric else IMP_SCALES
    for F,label in table:
        if F>=need: return F,label
    F=need*1.05
    return F, f"1:{int(round(F))}"


def _make_symbols(out):
    """Starter NEC symbol blocks, drawn in paper inches. Reused by the
    placement step later (scale = sheet factor × model units per inch)."""
    S="E-SYMB"
    def blk(name):
        if name in out.blocks: return None
        return out.blocks.new(name)
    b=blk("E-RECP-DUPLEX")
    if b:
        b.add_circle((0,0),0.11,dxfattribs={"layer":S})
        b.add_line((-0.11,0.04),(0.11,0.04),dxfattribs={"layer":S})
        b.add_line((-0.11,-0.04),(0.11,-0.04),dxfattribs={"layer":S})
    b=blk("E-RECP-GFCI")
    if b:
        b.add_circle((0,0),0.11,dxfattribs={"layer":S})
        b.add_line((-0.11,0.04),(0.11,0.04),dxfattribs={"layer":S})
        b.add_line((-0.11,-0.04),(0.11,-0.04),dxfattribs={"layer":S})
        b.add_text("GFCI",dxfattribs={"layer":S,"insert":(0.16,-0.04),"height":0.08})
    b=blk("E-SWCH-1P")
    if b: b.add_text("S",dxfattribs={"layer":S,"insert":(-0.06,-0.08),"height":0.18})
    b=blk("E-LITE-CLNG")
    if b:
        b.add_circle((0,0),0.13,dxfattribs={"layer":S})
        for dx,dy in ((0.13,0),(-0.13,0),(0,0.13),(0,-0.13)):
            b.add_line((dx,dy),(dx*1.5,dy*1.5),dxfattribs={"layer":S})
    b=blk("E-LITE-2X4")
    if b:
        b.add_lwpolyline([(-0.4,-0.2),(0.4,-0.2),(0.4,0.2),(-0.4,0.2)],close=True,dxfattribs={"layer":S})
        b.add_line((-0.4,0),(0.4,0),dxfattribs={"layer":S})
    b=blk("E-DATA")
    if b: b.add_lwpolyline([(-0.12,-0.1),(0.12,-0.1),(0,0.12)],close=True,dxfattribs={"layer":S})
    b=blk("E-JBOX")
    if b:
        b.add_circle((0,0),0.09,dxfattribs={"layer":S})
        b.add_text("J",dxfattribs={"layer":S,"insert":(-0.035,-0.05),"height":0.1})
    b=blk("E-PANL")
    if b:
        b.add_lwpolyline([(-0.3,-0.12),(0.3,-0.12),(0.3,0.12),(-0.3,0.12)],close=True,dxfattribs={"layer":S})
        for i in range(-2,3): b.add_line((i*0.1-0.06,-0.12),(i*0.1+0.06,0.12),dxfattribs={"layer":S})


def extract_plan(sheet_path, dxf_paths, dxf_stems):
    """Clean one architect sheet into a model-space-only plan document."""
    doc_a1 = ezdxf.readfile(str(sheet_path))

    xref_name=None; xref_ix=xref_iy=0.0; xref_sx=xref_sy=1.0; xref_rot=0.0
    for e in doc_a1.modelspace():
        try:
            if e.dxftype()=="INSERT" and e.dxf.name not in SKIP_BLOCKS:
                if _norm(e.dxf.name) in {_norm(s) for s in dxf_stems}:
                    xref_name=e.dxf.name
                    xref_ix=e.dxf.insert.x; xref_iy=e.dxf.insert.y
                    xref_sx=getattr(e.dxf,'xscale',1.0)
                    xref_sy=getattr(e.dxf,'yscale',1.0)
                    xref_rot=math.radians(getattr(e.dxf,'rotation',0.0))
                    break
        except: pass
    debug_info=[]
    if xref_name:
        debug_info.append(f"xref: ix={xref_ix:.1f} iy={xref_iy:.1f} sx={xref_sx:.4f}")
        txt_count=0; mtext_count=0; first_label=None
        for e in doc_a1.modelspace():
            try:
                if e.dxftype()=="TEXT":
                    txt_count+=1
                    if first_label is None and len(e.dxf.text.strip())>1:
                        mx,my=a1_to_master(e.dxf.insert.x,e.dxf.insert.y)
                        first_label=f"TEXT '{e.dxf.text.strip()[:15]}' a1=({e.dxf.insert.x:.0f},{e.dxf.insert.y:.0f})->master=({mx:.0f},{my:.0f})"
                elif e.dxftype()=="MTEXT":
                    mtext_count+=1
                    if first_label is None:
                        try:
                            txt=clean_mtext(e.text)
                            if len(txt)>1:
                                mx,my=a1_to_master(e.dxf.insert.x,e.dxf.insert.y)
                                first_label=f"MTEXT '{txt[:15]}' a1=({e.dxf.insert.x:.0f},{e.dxf.insert.y:.0f})->master=({mx:.0f},{my:.0f})"
                        except: pass
            except: pass
        debug_info.append(f"A-1 text: {txt_count} TEXT {mtext_count} MTEXT")
        if first_label: debug_info.append(first_label)

    def a1_to_master(ax,ay):
        dx=ax-xref_ix; dy=ay-xref_iy
        cr=math.cos(-xref_rot); sr=math.sin(-xref_rot)
        ux=dx*cr-dy*sr; uy=dx*sr+dy*cr
        return ux/xref_sx, uy/xref_sy

    if xref_name:
        master_path=next((p for p in dxf_paths if _norm(p.stem)==_norm(xref_name)),None)
        doc_m=ezdxf.readfile(str(master_path)) if master_path else doc_a1
    else:
        master_path=None
        doc_m=doc_a1



    msp_m=doc_m.modelspace()

    zones=[]; zone_src=[]; all_vp_centers=[]
    for layout in doc_a1.layouts:
        if layout.name=="Model": continue
        for e in layout:
            try:
                if e.dxftype()=="VIEWPORT":
                    vcp=getattr(e.dxf,'view_center_point',None)
                    vh=getattr(e.dxf,'view_height',None)
                    ps_w=getattr(e.dxf,'width',None)
                    ps_h=getattr(e.dxf,'height',None)
                    if vcp and vh and vh>0:
                        if xref_name:
                            mx,my=a1_to_master(vcp.x,vcp.y)
                        else:
                            mx,my=vcp.x,vcp.y
                        half_h=vh/2
                        aspect=(ps_w/ps_h) if (ps_w and ps_h and ps_h>0) else 1.5
                        half_w=half_h*aspect
                        if half_w<50 or half_h<50: continue
                        all_vp_centers.append((mx,my))
                        if _is_non_floor_layout(layout.name): continue
                        zones.append((mx-half_w,mx+half_w,my-half_h,my+half_h))
                        zone_src.append(layout.name)
            except: pass

    # ── Zone refinement: hone in on the building(s), not the whole sheet ────
    # 1. wall midpoints (any WALL/MURO/PARED layer, top level + 2 block levels)
    wall_pts=[]
    for ve,_ in iter_leaves(msp_m,doc_m,2,skip=set(SKIP)):
        if len(wall_pts)>=200000: break
        try:
            lay=getattr(ve.dxf,'layer','').upper()
            if not any(k in lay for k in ("WALL","MURO","PARED")): continue
            g=leaf_geom(ve,1.0)
            if g and g[0] in ("line","poly"): wall_pts.append(geom_mid(g))
        except: pass
    if len(wall_pts)<15:      # no recognisable wall layers → every line is structure
        wall_pts=[]
        for ve,_ in iter_leaves(msp_m,doc_m,2,skip=set(SKIP)):
            if len(wall_pts)>=200000: break
            try:
                g=leaf_geom(ve,1.0)
                if g and g[0]=="line": wall_pts.append(geom_mid(g))
            except: pass
    def _bb(pts):
        xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
        return (min(xs),max(xs),min(ys),max(ys))
    def _inside(z,p): return z[0]<=p[0]<=z[1] and z[2]<=p[1]<=z[3]
    def _area(z): return max(1e-9,(z[1]-z[0])*(z[3]-z[2]))
    def _contains(a,b):
        tx=0.02*(a[1]-a[0]); ty=0.02*(a[3]-a[2])
        return a[0]-tx<=b[0] and b[1]<=a[1]+tx and a[2]-ty<=b[2] and b[3]<=a[3]+ty
    def _cluster_walls(pts, cap=6):
        bx=_bb(pts); ext=max(bx[1]-bx[0],bx[3]-bx[2],1.0); cell=ext/60.0
        bins={}
        for p in pts:
            k=(int((p[0]-bx[0])/cell),int((p[1]-bx[2])/cell)); bins.setdefault(k,[]).append(p)
        seen=set(); comps=[]
        for k in bins:
            if k in seen: continue
            stack=[k]; seen.add(k); comp=[]
            while stack:
                cx_,cy_=stack.pop(); comp.extend(bins[(cx_,cy_)])
                for dx in (-1,0,1):
                    for dy in (-1,0,1):
                        nk=(cx_+dx,cy_+dy)
                        if nk in bins and nk not in seen: seen.add(nk); stack.append(nk)
            if len(comp)>=20: comps.append(comp)
        comps.sort(key=len,reverse=True)
        if not comps: return []
        top=len(comps[0]); out=[]
        # cap is how many NAMED floor-plan layouts survived the layout-name filter
        # (viewport center/geometry mismatch means we can't correlate clusters to
        # specific layouts, but a real floor has far more wall segments than a
        # roof outline or site boundary, so density rank is the next best signal)
        for comp in comps[:max(1,cap)]:
            if len(comp)<0.25*top: break
            b=_bb(comp); px=0.10*(b[1]-b[0]); py=0.10*(b[3]-b[2])
            out.append((b[0]-px,b[1]+px,b[2]-py,b[3]+py))
        return out

    # ── Coordinate-shift correction ──────────────────────────────────────────
    # Some converted DXFs carry a leftover origin offset: paper-space viewport
    # centers no longer line up with where modelspace geometry actually sits,
    # even though the drawing itself is internally consistent (every viewport
    # is off by the SAME vector). Detect this by checking whether any named
    # floor viewport contains a real share of the walls; if none do, solve for
    # the single shift that maps the viewport centroid onto the wall centroid
    # (using every viewport, not just named survivors, for a stable estimate)
    # and re-anchor the named zones there before scoring.
    def _inside_q(z,p): return z[0]<=p[0]<=z[1] and z[2]<=p[1]<=z[3]
    direct_ok = any(sum(1 for p in wall_pts if _inside_q(z,p))>=15 for z in zones)
    if not direct_ok and wall_pts and all_vp_centers and zones:
        wgx=sum(p[0] for p in wall_pts)/len(wall_pts); wgy=sum(p[1] for p in wall_pts)/len(wall_pts)
        vgx=sum(p[0] for p in all_vp_centers)/len(all_vp_centers); vgy=sum(p[1] for p in all_vp_centers)/len(all_vp_centers)
        sx,sy=wgx-vgx,wgy-vgy
        shifted=[(z[0]+sx,z[1]+sx,z[2]+sy,z[3]+sy) for z in zones]
        if any(sum(1 for p in wall_pts if _inside_q(z,p))>=15 for z in shifted):
            zones=shifted   # confirmed: use the corrected positions from here on

    n_before=len([z for z in zones if z[0]>-1e8 and z[1]<1e8])
    cand=[z for z in zones if z[0]>-1e8 and z[1]<1e8]
    # label positions in plan coordinates (the sheet's own text, xref-transformed)
    lab_pts=[]
    for e in doc_a1.modelspace():
        try:
            if e.dxftype() in ("TEXT","MTEXT"):
                t=(e.dxf.text if e.dxftype()=="TEXT" else clean_mtext(e.text)).strip()
                if len(t)<2: continue
                p=(e.dxf.insert.x,e.dxf.insert.y)
                lab_pts.append(a1_to_master(*p) if xref_name else p)
        except: pass
    scored=[(z,sum(1 for p in wall_pts if _inside(z,p)),sum(1 for p in lab_pts if _inside(z,p))) for z in cand]
    max_n=max([n for _,n,_ in scored] or [0])
    # a floor plan: a real share of the walls AND room labels on it
    # (elevations/sections/roof plans have few walls and no labels)
    scored=[(z,n,m) for z,n,m in scored if n>=max(15,0.2*max_n) and (m>=3 or len(lab_pts)<3)]
    keep=[]
    for z,n,m in scored:                                       # drop site/overall views
        container=any(z2 is not z and _contains(z,z2) and n2>=0.6*n for z2,n2,_ in scored)
        if not container: keep.append((z,n))
    dedup=[]
    for z,n in sorted(keep,key=lambda t:-_area(t[0])):         # largest first
        nested=any(_contains(z2,z) and n2>=2*n for z2,n2 in dedup)   # enlarged detail of a kept floor
        dup=any(_contains(z2,z) and _area(z)>0.8*_area(z2) for z2,_ in dedup)
        if not nested and not dup: dedup.append((z,n))
    # two views of the SAME floor (architect split the plan) overlap → merge them
    merged=[]
    for z,n in dedup:
        for i,(z2,n2) in enumerate(merged):
            ix=max(0,min(z[1],z2[1])-max(z[0],z2[0])); iy=max(0,min(z[3],z2[3])-max(z[2],z2[2]))
            if ix*iy>0.2*min(_area(z),_area(z2)):
                merged[i]=((min(z[0],z2[0]),max(z[1],z2[1]),min(z[2],z2[2]),max(z[3],z2[3])),n+n2); break
        else: merged.append((z,n))
    zones=[z for z,_ in merged]
    if not zones and wall_pts:
        zones=_cluster_walls(wall_pts, cap=len(cand) if cand else 6)
    if not zones:
        zones=[(-1e9,1e9,-1e9,1e9)]
    # Tighten only when this is NOT the simple known-good case: i.e. we had to
    # throw viewports away (multi-view sheet), or a lone viewport is a bare site
    # plan (≥10× the building). A single framed viewport, or viewport +
    # wall-cluster, is left exactly as it was.
    dropped=n_before>len(zones)
    tight=[]
    for z in zones:
        pts=[p for p in wall_pts if _inside(z,p)]
        if len(pts)>=15:
            b=_bb(pts)
            if dropped or _area(b)<0.10*_area(z):
                px=0.15*(b[1]-b[0]); py=0.15*(b[3]-b[2])
                z=(b[0]-px,b[1]+px,b[2]-py,b[3]+py)
        tight.append(z)
    zones=tight
    vp_zones=[z for z in cand]   # viewport zones in original order (names in zone_src)
    def _ov(a,b):
        ix=max(0,min(a[1],b[1])-max(a[0],b[0])); iy=max(0,min(a[3],b[3])-max(a[2],b[2])); return ix*iy
    zone_names=[]
    for z in zones:
        best=None; bo=0
        for zv,nm in zip(vp_zones,zone_src):
            o=_ov(z,zv)
            if o>bo: bo=o; best=nm
        zone_names.append(best if (best and not re.match(r"(?i)^layout\s*\d*$",best)) else "")
    # ── Floors the converter lost: dwg2dxf exports only the ACTIVE layout, so an
    # xref sheet may show one floor while the master holds more. Look for other
    # wall clusters in the same lot column and accept only those the sheet's own
    # labels land in (a site-plan copy of the house has no labels → rejected).
    if xref_name and zones:
        lx1=min(z[0] for z in zones); lx2=max(z[1] for z in zones); lw=lx2-lx1
        base_n=max(sum(1 for p in wall_pts if _inside(z,p)) for z in zones)
        pts=[p for p in wall_pts if lx1-0.05*lw<=p[0]<=lx2+0.05*lw and not any(_inside(z,p) for z in zones)]
        bands={}
        for p in pts:
            b=round(p[1]/200)*200; bands[b]=bands.get(b,0)+1
        clusters=[]; cur=[]
        for y in sorted(bands):
            if cur and y-cur[-1]>600: clusters.append(cur); cur=[]
            cur.append(y)
        if cur: clusters.append(cur)
        for cl in clusters:
            cz=(lx1,lx2,min(cl)-100,max(cl)+100)
            cp=[p for p in wall_pts if _inside(cz,p)]
            if len(cp)<max(15,0.2*base_n): continue
            b=_bb(cp); px=0.08*(b[1]-b[0]); py=0.08*(b[3]-b[2]); cz=(b[0]-px,b[1]+px,b[2]-py,b[3]+py)
            if sum(1 for p in lab_pts if _inside(cz,p))<3: continue
            if any(_ov(cz,z)>0 for z in zones): continue
            fh=max(z[3]-z[2] for z in zones); ccy=(cz[2]+cz[3])/2
            if min(abs(ccy-(z[2]+z[3])/2) for z in zones)>4*fh: continue    # another drawing set, not a floor
            zones.append(cz); zone_names.append("")
    # ── Split a zone that frames several plans side by side (one viewport
    # over "floor 1 | floor 2"): a wall-free gap with room labels on both sides
    def _split_zone(z):
        pts=[p for p in wall_pts if _inside(z,p)]
        if len(pts)<40: return [z]
        for axis in (0,1):
            vals=sorted(p[axis] for p in pts); span=vals[-1]-vals[0]
            if span<=0: continue
            best=None
            for a,b in zip(vals,vals[1:]):
                g=b-a
                if g>max(0.15*span,30.0) and (best is None or g>best[1]-best[0]): best=(a,b)
            if not best: continue
            lo=[p for p in pts if p[axis]<=best[0]]; hi=[p for p in pts if p[axis]>=best[1]]
            if len(lo)<0.3*len(pts) or len(hi)<0.3*len(pts): continue
            mid=(best[0]+best[1])/2
            ll=sum(1 for q in lab_pts if _inside(z,q) and q[axis]<mid); lh=sum(1 for q in lab_pts if _inside(z,q) and q[axis]>mid)
            if ll<3 or lh<3: continue
            if sum(1 for q in lab_pts if _inside(z,q) and best[0]<q[axis]<best[1]): continue   # labels in the gap → one plan
            z1=list(z); z2=list(z); z1[axis*2+1]=mid; z2[axis*2]=mid
            return _split_zone(tuple(z1))+_split_zone(tuple(z2))
        return [z]
    _nz=[]; _nn=[]
    for z,nm in zip(zones,zone_names):
        parts_=_split_zone(z)
        for i,p_ in enumerate(parts_):
            _nz.append(p_); _nn.append(nm if len(parts_)==1 else "")
    zones=_nz; zone_names=_nn
    # tighten each (possibly new) zone to its own walls
    _t=[]
    for z in zones:
        pts=[p for p in wall_pts if _inside(z,p)]
        if len(pts)>=15:
            b=_bb(pts); px=0.12*(b[1]-b[0]); py=0.12*(b[3]-b[2]); z=(b[0]-px,b[1]+px,b[2]-py,b[3]+py)
        _t.append(z)
    zones=_t
    zone_note=f"{len(zones)} floor zone(s), {len(wall_pts)} wall segs"
    zone_wbox=[]
    for z in zones:
        pts=[p for p in wall_pts if _inside(z,p)]
        zone_wbox.append(_bb(pts) if len(pts)>=15 else z)
    def looks_like_grid(g):
        """Axis-aligned line spanning ≥85% of the building in its direction
        and extending past the wall envelope: a grid line regardless of layer."""
        if g[0]!="line": return False
        (x1,y1),(x2,y2)=g[1],g[2]; dx,dy=abs(x2-x1),abs(y2-y1)
        for wb in zone_wbox:
            wx=wb[1]-wb[0]; wy=wb[3]-wb[2]
            if wx<=0 or wy<=0: continue
            if dy<=0.02*max(dx,1) and dx>=0.85*wx:
                if wb[2]<=y1<=wb[3] and (min(x1,x2)<wb[0]-0.05*wx or max(x1,x2)>wb[1]+0.05*wx): return True
            if dx<=0.02*max(dy,1) and dy>=0.85*wy:
                if wb[0]<=x1<=wb[1] and (min(y1,y2)<wb[2]-0.05*wy or max(y1,y2)>wb[3]+0.05*wy): return True
        return False

    zone_x_center=(min(z[0] for z in zones)+max(z[1] for z in zones))/2
    all_x1=min(z[0] for z in zones)-200; all_x2=max(z[1] for z in zones)+200
    all_y1=min(z[2] for z in zones)-200; all_y2=max(z[3] for z in zones)+200

    out=ezdxf.new("R2000", setup=True)     # setup=True loads CENTER/DASHED linetypes
    for lname,(col,lt,lw) in OUT_LAYERS.items():
        if lname not in out.layers:
            L=out.layers.new(lname)
            L.color=col; L.dxf.linetype=lt if lt in out.linetypes else "Continuous"
            L.dxf.lineweight=lw
            if lname=="DEFPOINTS": L.dxf.plot=0
    out_msp=out.modelspace()
    ec=[0]
    ins_units=int(doc_m.header.get("$INSUNITS",0) or 0)
    plan_w=max(z[1] for z in zones)-min(z[0] for z in zones)
    out.header["$LTSCALE"]=max(1.0,round(plan_w/120.0,2))
    out.header["$PSLTSCALE"]=0
    out.header["$CELTSCALE"]=1.0
    def A(src,et="LINE",closed=None):
        d={"layer":map_layer(src,et),"color":256}
        if closed is not None: d["closed"]=closed
        return d

    flat=max(plan_w/4000.0,0.01)
    zx=max(z[1] for z in zones)-min(z[0] for z in zones)
    zy=max(z[3] for z in zones)-min(z[2] for z in zones)
    ezones=[(X1-0.25*zx,X2+0.25*zx,Y1-0.25*zy,Y2+0.25*zy) for X1,X2,Y1,Y2 in zones]
    def inzone(x,y,zs=zones): return any(X1<=x<=X2 and Y1<=y<=Y2 for X1,X2,Y1,Y2 in zs)
    def bbox_hits(b,zs=zones):
        bx1,bx2,by1,by2=b
        return any(bx1<=X2 and bx2>=X1 and by1<=Y2 and by2>=Y1 for X1,X2,Y1,Y2 in zs)
    if xref_name:
        MAXD,CAP=6,80000
        def ib(x,y): return all_x1<=x<=all_x2 and all_y1<=y<=all_y2
    else:
        MAXD,CAP=6,None
        def ib(x,y): return True

    copied=0
    # Top-level entities in their own right, plus everything inside blocks.
    for ve,_ in iter_leaves(msp_m, doc_m, MAXD, skip=set(SKIP)):
        if CAP and ec[0]>CAP: break
        try:
            layer=getattr(ve.dxf,'layer','0')
            g=leaf_geom(ve,flat)
            if g is None: continue
            et="LWPOLYLINE" if g[0]=="poly" else ("CIRCLE" if g[0]=="circle" else ("ARC" if g[0]=="arc" else "LINE"))
            if is_grid_layer(layer) or looks_like_grid(g):
                layer="S-GRID"
                # grid lines run past the plan; bubbles sit just outside it
                ok = bbox_hits(geom_bbox(g)) if g[0] in ("line","poly") else inzone(*g[1],ezones)
            else:
                ok = inzone(*geom_mid(g)) and ib(*geom_mid(g))
            if not ok: continue
            write_leaf(out_msp,g,A(layer,et))
            ec[0]+=1
        except: pass

    # ── LABELS ────────────────────────────────────────────────────────────────
    placed_labels=0
    # If the architect tagged rooms on a dedicated layer (A-AREA-IDEN, ROOM…),
    # those ARE the room labels — use them and skip the guessing.
    _AREA_RX=re.compile(r"(AREA[-_]?IDEN|ROOM|\bRM\b|SPACE|AMBIENTE|LOCAL)",re.I)
    area_layers={getattr(e.dxf,'layer','') for e in doc_a1.modelspace()
                 if e.dxftype() in ("TEXT","MTEXT") and _AREA_RX.search(getattr(e.dxf,'layer',''))}
    def label_kind(txt,src_lay):
        if is_grid_layer(src_lay) and len(txt)<=4: return "grid"
        if area_layers:
            return "room" if src_lay in area_layers else None
        if is_grid_id(txt): return "gridid"
        return "room" if is_room_label(txt) else None
    if not xref_name:
        # Revit: text is at exact geometry coordinates in sheet file
        for e in doc_a1.modelspace():
            try:
                if e.dxftype() in ["TEXT","MTEXT"]:
                    if e.dxftype()=="TEXT":
                        txt=e.dxf.text.strip(); ix,iy=e.dxf.insert.x,e.dxf.insert.y
                        txt_rot=getattr(e.dxf,'rotation',0.0); h=e.dxf.height
                    else:
                        txt=clean_mtext(e.text); ix,iy=e.dxf.insert.x,e.dxf.insert.y
                        txt_rot=getattr(e.dxf,'rotation',0.0); h=getattr(e.dxf,'char_height',20)
                    if len(txt)<1: continue
                    src_lay=getattr(e.dxf,'layer','')
                    kind=label_kind(txt,src_lay)
                    if kind=="grid": lay_out,zs="S-GRID-IDEN",ezones
                    elif kind=="gridid":
                        if inzone(ix,iy,zones): continue     # ids off a grid layer live in the ring
                        lay_out,zs="S-GRID-IDEN",ezones
                    elif kind=="room": lay_out,zs="A-ANNO-TEXT",zones
                    else: continue
                    if inzone(ix,iy,zs):
                        if lay_out=="A-ANNO-TEXT": lay_out="A-AREA-IDEN"; txt="%%U"+txt
                        out_msp.add_text(txt[:50],dxfattribs={
                            "layer":lay_out,"color":256,
                            "insert":(ix,iy),"height":h,"rotation":txt_rot})
                        placed_labels+=1
            except: pass
    else:
        # Xref: the sheet's labels transform onto the plan exactly; place each one
        # inside the floor it lands in. No averaging, no offset.
        for e in doc_a1.modelspace():
            try:
                if e.dxftype() in ["TEXT","MTEXT"]:
                    if e.dxftype()=="TEXT":
                        txt=e.dxf.text.strip(); ix,iy=e.dxf.insert.x,e.dxf.insert.y
                        txt_rot=getattr(e.dxf,'rotation',0.0); h=e.dxf.height
                    else:
                        txt=clean_mtext(e.text); ix,iy=e.dxf.insert.x,e.dxf.insert.y
                        txt_rot=getattr(e.dxf,'rotation',0.0); h=getattr(e.dxf,'char_height',20)
                    if len(txt)<1: continue
                    src_lay=getattr(e.dxf,'layer','')
                    kind=label_kind(txt,src_lay)
                    if kind in ("grid","gridid"): lay_out,zs="S-GRID-IDEN",ezones
                    elif kind=="room": lay_out,zs="A-AREA-IDEN",zones
                    else: continue
                    mx,my=a1_to_master(ix,iy)
                    if not inzone(mx,my,zs): continue
                    if kind=="gridid" and inzone(mx,my,zones): continue    # ids live in the ring
                    if lay_out=="A-AREA-IDEN": txt="%%U"+txt
                    out_msp.add_text(txt[:50],dxfattribs={
                        "layer":lay_out,"color":256,"insert":(mx,my),
                        "height":h*xref_sx,"rotation":txt_rot-math.degrees(xref_rot)})
                    placed_labels+=1
            except: pass

    return dict(doc=out, zones=zones, zone_names=zone_names, ents=ec[0]+copied, labels=placed_labels,
                zone_note=zone_note, ins_units=ins_units, debug=debug_info,
                master_path=master_path, xref_name=xref_name, sheet_path=sheet_path,
                wall_pts=wall_pts, flat=flat, plan_w=plan_w)


# ═══════════════════════════════════════════════════════════════════════════
# RCP → ceiling geometry in plan coordinates
# ═══════════════════════════════════════════════════════════════════════════
def extract_rcp(rcp_path, dxf_paths, dxf_stems, zones, floor_keys, fbox, flat):
    """Return (scratch_doc, diag). scratch_doc modelspace holds ceiling geometry in
    plan coordinates on A-CLNG (grid/ceiling) and E-LITE-EQPM (fixtures)."""
    MG=50; _WALLKW=("WALL","MURO","PARED")
    RCP_SKIP_LAYERS={"A-WALL","A-WALL-PATT","A-FLOR","A-GLAZ-CURT","A-GLAZ-CWMG","I-WALL",
        "A-ANNO-DIMS","A-ANNO-DIMS-96","G-ANNO-NPLT","G-ANNO-TEXT","G-ANNO-TTLB",
        "G-ANNO-TTLB-WIDE","A-AREA-IDEN"}
    RCP_GRID={"S-GRID","S-GRID-IDEN"}
    RCP_SKIP_SUB=("WALL","MURO","PARED","DOOR","PUERTA","WINDOW","VENTANA","FURN","MUEBLE",
                  "PISO","FLOOR","STAIR","ESCAL","CASEWORK","PLUMB","SANIT")
    def is_ceiling_layer(n):
        if n in RCP_SKIP_LAYERS or n in RCP_GRID: return False
        u=n.upper()
        if any(k in u for k in CEIL_LAYER_KW): return True
        if any(k in u for k in RCP_SKIP_SUB): return False
        return True
    def _bbox(pts):
        xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
        return (min(xs),max(xs),min(ys),max(ys)) if pts else None
    def _overlap(a,b):
        if not a or not b: return 0.0
        ix=max(0,min(a[1],b[1])-max(a[0],b[0])); iy=max(0,min(a[3],b[3])-max(a[2],b[2]))
        aa=(a[1]-a[0])*(a[3]-a[2]); ab=(b[1]-b[0])*(b[3]-b[2])
        return (ix*iy)/max(1e-9,min(aa,ab))
    floor_cx=(min(z[0] for z in zones)+max(z[1] for z in zones))/2
    inzone=lambda x,y: any(X1<=x<=X2 and Y1<=y<=Y2 for X1,X2,Y1,Y2 in zones)

    scratch=ezdxf.new("R2000"); sm=scratch.modelspace()
    for ln,col in (("A-CLNG",9),("E-LITE-EQPM",33),("E-LITE-EQPM-IDEN",33)):
        scratch.layers.new(ln).color=col
    doc_rcp=ezdxf.readfile(str(rcp_path)); msp_rcp=doc_rcp.modelspace()
    src_doc,src_msp,src_name=doc_rcp,msp_rcp,"self"
    rx=None
    for e in msp_rcp:
        try:
            if e.dxftype()=="INSERT" and e.dxf.name not in SKIP_BLOCKS:
                nm=e.dxf.name.lower()
                if _norm(nm) in {_norm(s) for s in dxf_stems} and _norm(nm)!=_norm(rcp_path.stem):
                    rx=(nm,e.dxf.insert.x,e.dxf.insert.y,getattr(e.dxf,'xscale',1.0),
                        getattr(e.dxf,'yscale',1.0),math.radians(getattr(e.dxf,'rotation',0.0))); break
        except: pass
    def sheet_to_src(ax,ay):
        if not rx: return ax,ay
        _,ix_,iy_,sx_,sy_,rot_=rx; dx=ax-ix_; dy=ay-iy_
        cr=math.cos(-rot_); sr=math.sin(-rot_)
        return (dx*cr-dy*sr)/sx_,(dx*sr+dy*cr)/sy_
    if rx:
        p_src=next((p for p in dxf_paths if _norm(p.stem)==_norm(rx[0])),None)
        if p_src: src_doc=ezdxf.readfile(str(p_src)); src_msp=src_doc.modelspace(); src_name=p_src.name
    rcp_zones=[]
    for layout in doc_rcp.layouts:
        if layout.name=="Model": continue
        for e in layout:
            try:
                if e.dxftype()=="VIEWPORT":
                    vcp=getattr(e.dxf,'view_center_point',None); vh=getattr(e.dxf,'view_height',None)
                    ps_w=getattr(e.dxf,'width',None); ps_h=getattr(e.dxf,'height',None)
                    if vcp and vh and vh>0:
                        mx,my=sheet_to_src(vcp.x,vcp.y); half_h=vh/2
                        aspect=(ps_w/ps_h) if (ps_w and ps_h and ps_h>0) else 1.5
                        half_w=half_h*aspect
                        if half_w<50 or half_h<50: continue
                        rcp_zones.append((mx-half_w,mx+half_w,my-half_h,my+half_h))
            except: pass
    _insrc=(lambda x,y: any(X1<=x<=X2 and Y1<=y<=Y2 for X1,X2,Y1,Y2 in rcp_zones)) if rcp_zones else (lambda x,y: True)
    _all=[]
    for ve,_ in iter_leaves(src_msp,src_doc,4,skip=set(SKIP)):
        if len(_all)>=40000: break
        try:
            g=leaf_geom(ve,flat)
            if g is None or g[0] not in ("line","poly"): continue
            m=geom_mid(g)
            if not _insrc(*m): continue
            lay=getattr(ve.dxf,'layer','').upper()
            _all.append((m[0],m[1],any(k in lay for k in _WALLKW)))
        except: pass
    _wpts=[(x,y) for x,y,w in _all if w]
    rbox=_bbox(_wpts) if len(_wpts)>=20 else _bbox([(x,y) for x,y,_ in _all])
    tx=ty=0.0
    if rbox and _overlap(rbox,fbox)<0.3:
        tx=(fbox[0]+fbox[1])/2-(rbox[0]+rbox[1])/2; ty=(fbox[2]+fbox[3])/2-(rbox[2]+rbox[3])/2
    score_mirror=sum((round((2*floor_cx-(x+tx))/MG),round((y+ty)/MG)) in floor_keys for x,y,_ in _all)
    score_align =sum((round((x+tx)/MG),round((y+ty)/MG)) in floor_keys for x,y,_ in _all)
    use_mirror = not (score_align>=5 and score_align>score_mirror)
    def T(x,y):
        x2=x+tx; y2=y+ty
        return ((2*floor_cx-x2) if use_mirror else x2), y2
    n=0; nfix=0
    from ezdxf.addons import Importer
    _imp=Importer(src_doc,scratch); _imported=set()
    FIXKW=("LITE","LIGHT","LAMP","LUM","FIXT","INL","LUZ","ILUMIN")
    def is_fixture_insert(e):
        try:
            u=(getattr(e.dxf,'layer','')+" "+e.dxf.name).upper()
            if not any(k in u for k in FIXKW): return False
            blk=src_doc.blocks.get(e.dxf.name)
            return blk is not None and sum(1 for x in blk)<=400
        except Exception: return False
    def fixture_type(name):
        # "INL-1 - INL-1-5338199-REFLECTING CEILING PLAN A" → "INL-1"; "Light-2a" → "Light-2a"
        t=name.split(" - ")[0].strip(); return re.sub(r"[-_ ]?\d{6,}.*$","",t)[:12] or name[:12]
    for ve,in_ceil in iter_leaves(src_msp,src_doc,5,ceil_fn=is_ceiling_layer,skip=set(SKIP),keep_insert=is_fixture_insert):
        if n>60000: break
        try:
            lay=getattr(ve.dxf,'layer','0')
            if ve.dxftype()=="INSERT":
                p=ve.dxf.insert; m=(p.x,p.y)
                if not (_insrc(*m) and inzone(*T(*m))): continue
                nm=ve.dxf.name
                if nm not in _imported:
                    try: _imp.import_block(nm); _imp.finalize(); _imported.add(nm)
                    except Exception: continue
                rot=getattr(ve.dxf,'rotation',0.0); sx=getattr(ve.dxf,'xscale',1.0); sy=getattr(ve.dxf,'yscale',1.0)
                if use_mirror: rot=-rot; sx=-sx
                ins=sm.add_blockref(nm,T(*m),dxfattribs={"layer":"E-LITE-EQPM","color":256,"rotation":rot,"xscale":sx,"yscale":sy})
                sm.add_text(fixture_type(nm),dxfattribs={"layer":"E-LITE-EQPM-IDEN","color":256,"height":6.0,
                            "insert":(T(*m)[0]+12.0,T(*m)[1]+12.0)})
                nfix+=1; continue
            if lay in RCP_GRID: continue
            if not in_ceil and not is_ceiling_layer(lay): continue
            g=leaf_geom(ve,flat)
            if g is None: continue
            m=geom_mid(g)
            if not (_insrc(*m) and inzone(*T(*m))): continue
            u=lay.upper()
            target="E-LITE-EQPM" if any(k in u for k in ("LITE","LIGHT","LAMP","LUM")) else "A-CLNG"
            write_leaf(sm,g,{"layer":target,"color":256},PT=T,reflect=use_mirror); n+=1
        except: pass
    diag=(f"{rcp_path.name} [src={src_name}, vp={len(rcp_zones)}, pts={len(_all)}, walls={len(_wpts)}, "
          f"shift=({tx:.0f},{ty:.0f}), align={score_align}/mirror={score_mirror} → "
          f"{'mirrored' if use_mirror else 'aligned'}, added={n} + {nfix} fixture blocks]")
    return scratch, diag

# ═══════════════════════════════════════════════════════════════════════════
# MARCO — the architect's border/title block, the way the engineer keeps it
# ═══════════════════════════════════════════════════════════════════════════
_FIRM_RX=re.compile(r"(\btel[:.\s]|\bphone|\bfax\b|e-?mail|@|\bllc\b|\binc\.?\b|\bpsc\b|\bcsp\b|\bcorp\.?\b|"
                    r"\bcel[:.\s]|\bcell\b|celular|m[oó]vil|cad services?|\bpmb\b|\bp\.?o\.? box\b|www\.|\.com\b)",re.I)
_DROP_RX=re.compile(r"(construction drawings? for|drawings? for:|prepared for:)",re.I)
_LBL_SHEET=("SHEET NO","SHEET NUMBER","SHEET #","HOJA","SHEET:")
_LBL_TITLE=("DRAWING TITLE","SHEET TITLE","TITLE","TITULO","TÍTULO","DRAWING NAME")
_LBL_DWGNO=("DRAWING NO","DWG NO","DWG. NO","DRAWING #")
_LBL_SCALE=("SCALE","ESCALA")
_LBL_DATE=("DATE","FECHA")
_PLOT_KEYS=("paper_width","paper_height","plot_rotation","left_margin","right_margin","top_margin",
            "bottom_margin","plot_origin_x_offset","plot_origin_y_offset","plot_paper_units","plot_type",
            "paper_size","plot_configuration_file","current_style_sheet","scale_numerator","scale_denominator",
            "standard_scale_type","plot_layout_flags","plot_window_x1","plot_window_y1","plot_window_x2","plot_window_y2")

def _default_info():
    return dict(found=False, src=None, paper=(36.0,24.0), plot={}, vp_rect=(16.46,12.81,31.77,21.28),
                sheetno=dict(insert=(34.38,1.12),rot=0.0,h=0.236,w=0.94,att=1,style="Standard"),
                title=dict(insert=(33.9,2.95),rot=0.0,h=0.118,w=2.5,att=2,style="Standard"),
                dwgno=None, scale=None, styles=[], right=35.5, top=23.5)

def extract_marco(sheet_path):
    """Pull the border out of the architect's sheet as drawn (styles, rotation,
    widths), explode it, drop logo/firm identity, keep the frame and the
    project-constant fields. Per-sheet fields (sheet no, title, drawing no,
    scale, date) are recognised by the label they sit next to and reported
    back so E-Electrical can write them on every sheet in the same spot."""
    import datetime
    from ezdxf.addons import Importer
    a=ezdxf.readfile(str(sheet_path))
    marco=ezdxf.new("R2000",setup=True); mm=marco.modelspace()
    for ln,col in (("G-ANNO-TTLB",6),("G-ANNO-TTLB-WIDE",214),("E-TEXTR",3),("E-MEDIUM",7),("E-TEXTS",2),("SHT-TXT1",4)):
        if ln not in marco.layers: marco.layers.new(ln).color=col
    info=_default_info(); info["src"]=a
    # the sheet = the layout with the largest viewport
    best=None; bvp=None; best_area=0
    for lay in a.layouts:
        if lay.name=="Model": continue
        for e in lay:
            if e.dxftype()=="VIEWPORT" and e.dxf.id!=1:
                ar=e.dxf.width*e.dxf.height
                if ar>best_area: best_area=ar; best=lay; bvp=e
    if best is None: return marco, info
    info["vp_rect"]=(bvp.dxf.center.x,bvp.dxf.center.y,bvp.dxf.width,bvp.dxf.height)
    info["plot"]={k:best.dxf.get(k) for k in _PLOT_KEYS if best.dxf.hasattr(k)}
    pw=best.dxf.paper_width/25.4 if best.dxf.hasattr("paper_width") else 0.0
    ph=best.dxf.paper_height/25.4 if best.dxf.hasattr("paper_height") else 0.0
    info["paper"]=(max(pw,ph),min(pw,ph)) if pw>1 and ph>1 else (36.0,24.0)
    # border block = INSERT with the largest extents
    def ins_extent(e):
        xs=[];ys=[]
        try:
            for ve in e.virtual_entities():
                g=leaf_geom(ve,0.05)
                if g: b=geom_bbox(g); xs+=[b[0],b[1]]; ys+=[b[2],b[3]]
        except: pass
        return (min(xs),max(xs),min(ys),max(ys)) if xs else None
    border=None; bext=None
    for e in best:
        if e.dxftype()=="INSERT":
            ext=ins_extent(e)
            if ext and (bext is None or (ext[1]-ext[0])*(ext[3]-ext[2])>(bext[1]-bext[0])*(bext[3]-bext[2])):
                border,bext=e,ext
    loose_paper=None   # set when the frame is loose paper-space entities, not a block
    if border is None:
        # No reusable title-block: some architects draw the frame as raw LINE/
        # LWPOLYLINE/TEXT directly on the sheet. Use those entities themselves.
        xs=[];ys=[]
        for e in best:
            if e.dxftype()=="VIEWPORT": continue
            try:
                g=leaf_geom(e,0.05)
                if g: b=geom_bbox(g); xs+=[b[0],b[1]]; ys+=[b[2],b[3]]
                elif e.dxftype() in ("TEXT","MTEXT"):
                    p=e.dxf.insert; xs+=[p.x]; ys+=[p.y]
            except: pass
        if not xs: return marco, info
        bext=(min(xs),max(xs),min(ys),max(ys))
        loose_paper=[e for e in best if e.dxftype()!="VIEWPORT"]
    info["found"]=True
    vx1=bvp.dxf.center.x-bvp.dxf.width/2; vx2=bvp.dxf.center.x+bvp.dxf.width/2
    vy1=bvp.dxf.center.y-bvp.dxf.height/2; vy2=bvp.dxf.center.y+bvp.dxf.height/2
    right=max(bext[1],vx2); top=max(bext[3],vy2); info["right"]=right; info["top"]=top
    logo=None
    imp=Importer(a,marco)
    if loose_paper is not None:
        # no block to explode: the layout's own entities ARE the frame
        imp.import_entities(loose_paper,mm); imp.finalize()
        for e in list(mm):
            if e.dxftype() in ("IMAGE","WIPEOUT","ATTDEF"): mm.delete_entity(e)
    else:
        try:
            for x in a.blocks[border.dxf.name]:
                if x.dxftype()=="IMAGE":
                    p=x.dxf.insert; logo=(p.x+border.dxf.insert.x,p.y+border.dxf.insert.y); break
        except: pass
        # bring everything across WITH styles, then explode the border
        imp.import_block(border.dxf.name)
        ins=mm.add_blockref(border.dxf.name,(border.dxf.insert.x,border.dxf.insert.y),
                            dxfattribs={"xscale":border.dxf.xscale,"yscale":border.dxf.yscale,"rotation":border.dxf.rotation})
        for at in (border.attribs or []):
            try: ins.add_attrib(at.dxf.tag or "*",at.dxf.text,at.dxf.insert,dxfattribs={"height":at.dxf.height,"rotation":at.dxf.rotation,"style":at.dxf.style,"layer":at.dxf.layer})
            except Exception: pass
        loose=[e for e in best if e.dxftype() in ("MTEXT","TEXT")]
        imp.import_entities(loose,mm); imp.finalize()
        ins.explode()
        for e in list(mm):
            if e.dxftype() in ("IMAGE","WIPEOUT","ATTDEF"): mm.delete_entity(e)
    loose_h={e.dxf.handle for e in mm if e.dxftype() in ("MTEXT","TEXT")}
    def txt_of(e): return (clean_mtext(e.text) if e.dxftype()=="MTEXT" else e.dxf.text).strip()
    def pos_of(e): return e.dxf.insert.x,e.dxf.insert.y
    def h_of(e): return e.dxf.char_height if e.dxftype()=="MTEXT" else e.dxf.height
    texts=[e for e in mm if e.dxftype() in ("MTEXT","TEXT")]
    # firm identity: contact lines and everything clustered around them (+ logo zone)
    anchors=[pos_of(e) for e in texts if _FIRM_RX.search(txt_of(e))]
    if logo: anchors.append(logo)
    def near_firm(x,y): return any(abs(x-ax)<=1.0 and abs(y-ay)<=1.0 for ax,ay in anchors)
    _LABELISH=re.compile(r"(SHEET|HOJA|DRAWING|DWG|PROJECT|PROYECTO|TITLE|TITULO|TÍTULO|SCALE|ESCALA|DATE|FECHA|"
                         r"DRAWN|DESIGNED|CHECKED|REVIEWED|MANAGER|ADDRESS|CLIENT|OWNER|ARCHITECT|ENGINEER|NUMBER|NO\.)")
    def is_label(s):
        u=s.strip().upper()
        return u.endswith(":") or u in ("OF","DE") or (len(u)<=20 and u.endswith(".") and bool(_LABELISH.search(u)))
    labels=[(txt_of(e).upper().rstrip(":.").strip(),*pos_of(e),e.dxf.rotation) for e in texts if is_label(txt_of(e))]
    def field_of(x,y,rot):
        best_l=None; bd=9e9
        for lt,lx,ly,lrot in labels:
            if abs(rot-90)<1 and abs(lrot-90)<1:              # rotated strip: values run along +y
                if -0.25<=y-ly<=6.0 and abs(x-lx)<=2.5: d=(y-ly)+0.3*abs(x-lx)
                else: continue
            elif abs(rot)<1 and abs(lrot)<1:                   # normal: label sits just above its value
                if abs(x-lx)<=1.8 and -0.3<=ly-y<=1.5: d=abs(ly-y)+1.5*abs(x-lx)   # stay in your column
                else: continue
            else: continue
            if d<bd: bd=d; best_l=lt
        return best_l or ""
    today=datetime.date.today().strftime("%Y-%b-%d").upper()
    _DATE_RX=re.compile(r"(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{4}[-\s][A-Z]{3}[-\s]\d{1,2}|[A-Z]{3,9}\.? ?\d{1,2},? ?\d{4})",re.I)
    title_specs=[]; used_styles=set()
    def spec(e):
        x,y=pos_of(e); rot=e.dxf.rotation
        if e.dxftype()=="MTEXT": att=e.dxf.attachment_point; w=e.dxf.width
        else: att=7; w=0.0                                     # TEXT/ATTRIB: baseline-left
        return dict(insert=(x,y),rot=rot,h=h_of(e),w=w,att=att,style=e.dxf.style)
    for e in texts:
        s=txt_of(e); x,y=pos_of(e)
        if e.dxf.handle in loose_h and x<vx2-0.5 and y<vy2+0.5 and x>vx1-0.5: mm.delete_entity(e); continue   # view titles inside the plan area
        if not s: continue
        if _DROP_RX.search(s): mm.delete_entity(e); continue
        if near_firm(x,y) and not is_label(s): mm.delete_entity(e); continue
        if e.dxftype()=="MTEXT" and e.dxf.width and x+e.dxf.width>right-0.1 and e.dxf.rotation==0:
            e.dxf.width=max(0.5,right-0.1-x)
        if is_label(s): continue
        fld=field_of(x,y,e.dxf.rotation)
        used_styles.add(e.dxf.style)
        if any(k in fld for k in _LBL_SHEET):          info["sheetno"]={**spec(e),"_found":True}; mm.delete_entity(e)
        elif any(k in fld for k in _LBL_TITLE):        title_specs.append(spec(e)); mm.delete_entity(e)
        elif any(k in fld for k in _LBL_DWGNO):        info["dwgno"]=spec(e); mm.delete_entity(e)
        elif any(k in fld for k in _LBL_SCALE) and ("=" in s or ":" in s or "1/" in s or s.upper().startswith("AS")):
            info["scale"]=spec(e); mm.delete_entity(e)
        elif any(k in fld for k in _LBL_DATE) and _DATE_RX.search(s):
            if e.dxftype()=="MTEXT": e.text=today
            else: e.dxf.text=today
    # empty attribute slots (no text) are just gone; orphaned note headers too
    for e in list(mm):
        if e.dxftype() in ("TEXT","MTEXT") and not txt_of(e): mm.delete_entity(e)
    alive=[e for e in mm if e.dxftype() in ("TEXT","MTEXT")]
    for e in alive:
        s=txt_of(e).upper()
        if "NOTE" in s.replace(" ","") and is_label(s):
            x,y=pos_of(e)
            if not any(o is not e and not is_label(txt_of(o)) and math.hypot(pos_of(o)[0]-x,pos_of(o)[1]-y)<1.6 for o in alive):
                mm.delete_entity(e)
    if info.get("dwgno") and info["sheetno"].get("_found"):
        info["dwgno"],info["sheetno"]=info["sheetno"],info["dwgno"]    # id ↔ index
    if title_specs:
        rot=title_specs[0]["rot"]
        if abs(rot-90)<1:            # rotated strip: lines advance in +x, pick the first slot
            t=min(title_specs,key=lambda d:d["insert"][0]); ymax=top
            for lt,lx,ly,_r in labels:
                if ly>t["insert"][1]+0.5 and abs(lx-t["insert"][0])<2.5: ymax=min(ymax,ly)
            t=dict(t); t["w"]=max(2.0,ymax-t["insert"][1]-0.4); t["att"]=7
        else:
            t=max(title_specs,key=lambda d:d["insert"][1]); t=dict(t)
            if t["att"]==7: t["insert"]=(t["insert"][0],t["insert"][1]+t["h"]); t["att"]=1
            if not t["w"]: t["w"]=max(2.0,right-0.3-t["insert"][0])
        info["title"]=t
    info["styles"]=[s_ for s_ in used_styles if s_ in a.styles]
    return marco, info

# ═══════════════════════════════════════════════════════════════════════════
# E-ELECTRICAL PLAN — the project file: xrefs x-plan + x-marco, copies, sheets
# ═══════════════════════════════════════════════════════════════════════════
E_LAYERS={"E-EQUIP":4,"E-EQUIP-EXIST":4,"E-HEAVY":4,"E-MEDIUM":7,"E-LIGHT":2,"E-XLIGHT":1,
          "E-LITE-EQPM":33,"E-LITE-EQPM-IDEN":33,"E-SWITCH":4,"E-TEXTL":6,"E-TEXTM":4,
          "E-TEXTR":3,"E-TEXTS":2,"E-DIM":2,"E-Wiring":4,"E-WIRING-F":4,"E-WIRINGF":4,
          "E-WIREPOLES":3,"0-Vport":4,"G-ANNO-TTLB":6,"G-ANNO-NPLT":7,"A-CLNG":9,"E-SYMB":7,"E-LITE-EQPM-IDEN":33}
E_LINETYPES={"E-WIRING-F":"DASHDOT","E-WIRINGF":"HIDDEN2","E-WIREPOLES":"DASHDOT2"}
PLAN_SHEETS=[("E-101","ELECTRICAL LIGHTING PLAN"),("E-102","ELECTRICAL POWER PLAN"),
             ("E-103","DATA/TELECOMMUNICATIONS PLAN"),("E-104","FIRE ALARM SYSTEM PLAN")]
NOTE_SHEETS_BEFORE=[("E-001","LEGEND AND NOTES"),("E-002","FIRE ALARM SYSTEM LEGEND AND NOTES")]
NOTE_SHEETS_AFTER=[("E-601","ONE LINE DIAGRAM"),("E-602","PANELBOARDS SCHEDULE"),
                   ("E-603","LIGHTING FIXTURES AND EQUIPMENT SCHEDULES")]

STD_SHEETS=[(36.0,24.0),(42.0,30.0),(48.0,36.0),(34.0,22.0),(24.0,18.0),(22.0,17.0),(17.0,11.0),(11.0,8.5)]
def snap_sheet(w,h):
    if w<h: w,h=h,w
    return min(STD_SHEETS,key=lambda s:abs(s[0]-w)+abs(s[1]-h))

def build_electrical(union_zone, rcp_scratch, marco_info, ins_units, project, split_parts, fbox=None, floors=None):
    import datetime
    E=ezdxf.new("R2000",setup=True); msp=E.modelspace()
    src=marco_info.get("src")
    if src is not None:
        try:
            from ezdxf.addons import Importer
            imp=Importer(src,E)
            want={marco_info["sheetno"]["style"],marco_info["title"]["style"]}|set(marco_info.get("styles",[]))
            for d in (marco_info.get("dwgno"),marco_info.get("scale")):
                if d: want.add(d["style"])
            imp.import_table("styles",entries=[s_ for s_ in want if s_ in src.styles]); imp.finalize()
        except Exception: pass
    def _style(name): return name if name in E.styles else "Standard"
    for ln,col in E_LAYERS.items():
        if ln not in E.layers:
            Lr=E.layers.new(ln); Lr.color=col
            if ln in E_LINETYPES and E_LINETYPES[ln] in E.linetypes: Lr.dxf.linetype=E_LINETYPES[ln]
    E.header["$INSUNITS"]=ins_units or 1
    E.header["$PSLTSCALE"]=1
    # xrefs — RELATIVE paths so the folder can live anywhere
    E.add_xref_def(filename="x-plan.dwg",name="x-plan")
    E.add_xref_def(filename="x-marco.dwg",name="x-marco")

    PW,PH=snap_sheet(*marco_info.get("paper",(36.0,24.0)))
    RX,RY,RW,RH=marco_info.get("vp_rect",(16.46,12.81,31.77,21.28))   # the architect's drawing area
    u_per_in=UNIT_PER_IN.get(ins_units,1.0)
    X1,X2,Y1,Y2=union_zone
    # floors: (name, box) — each framed on its own building extents
    if not floors: floors=[("",fbox if fbox else union_zone)]
    boxes=[b for _,b in floors]
    bw=max((b[1]-b[0]) for b in boxes)*1.06; bh=max((b[3]-b[2]) for b in boxes)*1.06
    cx=(X1+X2)/2; cy=(Y1+Y2)/2
    # plan viewport = the architect's drawing area, less a strip at the bottom for the view title
    VPw,VPh=RW,RH-1.4; VPc=(RX,RY+0.7)
    need=max(bw/(VPw*u_per_in),bh/(VPh*u_per_in))
    F,scale_label=pick_scale(need,ins_units)
    if ins_units not in UNIT_PER_IN: scale_label+=" (VERIFY UNITS)"
    view_w=VPw*F*u_per_in
    # one x-plan copy per discipline, in a row; one sheet per discipline per floor
    spacing=view_w*1.2
    copies=[]; n=0
    for k,(num,title) in enumerate(PLAN_SHEETS):
        ox=k*spacing; oy=0.0
        msp.add_blockref("x-plan",(ox,oy))
        for fname,b in floors:
            n+=1
            t=f"{title} - {fname}" if fname else title
            copies.append((f"E-1{n:02d}",t,((b[0]+b[1])/2+ox,(b[2]+b[3])/2+oy)))
    # RCP content sits on the LIGHTING copy (copy 0), like the engineer's
    if rcp_scratch is not None:
        ox,oy=0.0,0.0
        from ezdxf.addons import Importer
        blocks=[e for e in rcp_scratch.modelspace() if e.dxftype() in ("INSERT","TEXT")]
        if blocks:
            try:
                _i=Importer(rcp_scratch,E); _i.import_entities(blocks,msp); _i.finalize()
                for e in list(msp)[-len(blocks):]:
                    try: e.translate(ox,oy,0)
                    except Exception: pass
            except Exception: pass
        for e in rcp_scratch.modelspace():
            if e.dxftype() in ("INSERT","TEXT"): continue
            g=leaf_geom(e,0.5)
            if g: write_leaf(msp,g,{"layer":e.dxf.layer,"color":256},PT=lambda x,y:(x+ox,y+oy))
    # sheet-size model windows for legend / notes / schedules, in a row below
    winF=F; win_w=VPw*winF*u_per_in; win_h=23.0*winF*u_per_in
    row_cy=Y1-1.5*win_h; wins=[]
    for k,(num,title) in enumerate(NOTE_SHEETS_BEFORE+NOTE_SHEETS_AFTER):
        wcx=X1+k*win_w*1.33+win_w/2
        wins.append((num,title,(wcx,row_cy)))
        th=0.188*winF*u_per_in
        msp.add_text(title,dxfattribs={"layer":"E-TEXTR","color":256,"height":th,
                     "insert":(wcx-win_w/2+0.06*win_w,row_cy+win_h/2-0.08*win_h)})
    # starter legend + general notes in the E-001 window
    _make_symbols(E); S=winF*u_per_in
    wcx,wcy=wins[0][2]; x0=wcx-win_w/2+0.06*win_w; y=wcy+win_h/2-0.16*win_h
    msp.add_text("LEGEND",dxfattribs={"layer":"E-TEXTR","color":256,"height":0.14*S,"insert":(x0,y)})
    y-=0.45*S
    for bname,desc in [("E-RECP-DUPLEX","DUPLEX RECEPTACLE, 20A-125V, NEMA 5-20R"),("E-RECP-GFCI","DUPLEX RECEPTACLE, GFCI"),
                       ("E-SWCH-1P","SINGLE POLE SWITCH"),("E-LITE-CLNG","CEILING MOUNTED LIGHT FIXTURE"),
                       ("E-LITE-2X4","2'x4' RECESSED LED TROFFER"),("E-DATA","DATA / TELECOM OUTLET"),
                       ("E-JBOX","JUNCTION BOX"),("E-PANL","PANELBOARD, SURFACE MOUNTED")]:
        msp.add_blockref(bname,(x0+0.3*S,y),dxfattribs={"layer":"E-EQUIP","color":256,"xscale":S,"yscale":S})
        msp.add_text(desc,dxfattribs={"layer":"E-TEXTR","color":256,"height":0.094*S,"insert":(x0+1.0*S,y-0.05*S)})
        y-=0.5*S
    gx=wcx-win_w/2+0.45*win_w; gy=wcy+win_h/2-0.16*win_h
    msp.add_text("GENERAL NOTES",dxfattribs={"layer":"E-TEXTR","color":256,"height":0.14*S,"insert":(gx,gy)})
    gy-=0.45*S
    for i,n in enumerate([
        "ALL WORK SHALL COMPLY WITH THE NATIONAL ELECTRICAL CODE (NFPA 70), LATEST ADOPTED EDITION, AND ALL LOCAL AMENDMENTS.",
        "CONTRACTOR SHALL VERIFY ALL EXISTING CONDITIONS AND DIMENSIONS IN THE FIELD PRIOR TO ROUGH-IN.",
        "ALL RECEPTACLES IN KITCHENS, BATHROOMS, GARAGES, OUTDOORS AND WITHIN 6 FT OF SINKS SHALL BE GFCI PROTECTED PER NEC 210.8.",
        "PROVIDE EQUIPMENT GROUNDING CONDUCTOR IN ALL RACEWAYS. SIZE PER NEC 250.122.",
        "MOUNTING HEIGHTS (TO CENTERLINE, U.N.O.): RECEPTACLES 18\", SWITCHES 48\", COUNTER RECEPTACLES 42\".",
        "COORDINATE ALL LIGHT FIXTURE LOCATIONS WITH THE REFLECTED CEILING PLAN AND MECHANICAL EQUIPMENT.",
        "ARCHITECTURAL BACKGROUND SHOWN FOR REFERENCE ONLY. REFER TO ARCHITECTURAL DRAWINGS FOR DIMENSIONS.",
    ],1):
        msp.add_text(f"{i}.  {n}",dxfattribs={"layer":"E-TEXTR","color":256,"height":0.094*S,"insert":(gx,gy)}); gy-=0.32*S

    # ── paper space sheets ──────────────────────────────────────────────────
    from ezdxf.enums import MTextEntityAlignment as MA

    SN=marco_info["sheetno"]; TT=marco_info["title"]; DN=marco_info.get("dwgno"); SC=marco_info.get("scale")
    total_sheets=[0]
    def put(lay,text,sp,extra=None):
        h=sp["h"]; att=sp["att"]; w=sp["w"] or 0
        m=lay.add_mtext(text,dxfattribs={"layer":"G-ANNO-TTLB","color":256,"char_height":h,"style":_style(sp["style"]),
                                         **({"width":w} if w else {})})
        m.set_location(sp["insert"],rotation=sp["rot"],attachment_point=att)
        return m
    def sheet(num,title,view_center,view_h,vp_center,vp_size,plan=False):
        lay=E.layouts.new(num)
        plot=marco_info.get("plot") or {}
        if plot and (plot.get("paper_width") or 0)>0:
            for k,v in plot.items():
                try: lay.dxf.set(k,v)
                except Exception: pass
        else:
            lay.page_setup(size=(PH,PW),margins=(0.126,0.126,0.126,0.126),units="inch",rotation=1)
            lay.dxf.current_style_sheet="normal-plotter.ctb"
        lay.add_blockref("x-marco",(0,0),dxfattribs={"layer":"0"})
        put(lay,num,SN)
        put(lay,title,TT)
        if DN: put(lay,str(len(made)+1),DN)
        if SC: put(lay,scale_label if plan else "AS SHOWN",SC)
        lay.add_viewport(center=vp_center,size=vp_size,view_center_point=view_center,view_height=view_h,
                         dxfattribs={"layer":"0-Vport","status":2})
        if plan:
            vx=vp_center[0]-vp_size[0]/2+2.0; vy=vp_center[1]-vp_size[1]/2-0.55
            lay.add_arc((vx,vy),0.125,0,180,dxfattribs={"layer":"G-ANNO-TTLB","color":256})
            lay.add_arc((vx,vy),0.125,180,360,dxfattribs={"layer":"G-ANNO-TTLB","color":256})
            lay.add_line((vx,vy),(vx+2.7,vy),dxfattribs={"layer":"G-ANNO-NPLT","color":256})
            for s_,dx,dy in ((title,0.08,0.16),(scale_label,0.07,-0.04),("1",-0.17,0.07)):
                mt=lay.add_mtext(s_,dxfattribs={"layer":"G-ANNO-TTLB","color":256,"char_height":0.125,"style":_style(TT["style"])})
                mt.set_location((vx+dx,vy+dy),attachment_point=MA.TOP_LEFT if s_!="1" else MA.MIDDLE_CENTER)
        return num
    made=[]
    NPc=(RX,RY); NPw,NPh=RW,RH
    for num,title,(wcx,wcy) in wins[:len(NOTE_SHEETS_BEFORE)]:
        made.append(sheet(num,title,(wcx,wcy),NPh*winF*u_per_in,NPc,(NPw,NPh)))
    for num,title,vc in copies:
        made.append(sheet(num,title,vc,VPh*F*u_per_in,VPc,(VPw,VPh),plan=True))
    for num,title,(wcx,wcy) in wins[len(NOTE_SHEETS_BEFORE):]:
        made.append(sheet(num,title,(wcx,wcy),NPh*winF*u_per_in,NPc,(NPw,NPh)))
    for junk in ("Layout1","Layout2"):
        try:
            if junk in E.layouts: E.layouts.delete(junk)
        except: pass
    return E, made, scale_label, F

# ═══════════════════════════════════════════════════════════════════════════
# LIGHTING DESIGN — switches, switching letters, conduit runs (engineer's symbols)
# ═══════════════════════════════════════════════════════════════════════════
import json as _json
CCA_SYMBOLS=_json.loads('''{"Switch":{"base":[0.0,0.0],"ents":[["A",[0.0842,0.0002],0.027,90.0,0.0],["A",[0.1382,0.0002],0.027,270.0,180.0],["L",[0.0572,0.0002],[0.016,0.0002]]],"bbox":[0.016,0.165,-0.027,0.027]},"Switch-3W":{"base":[0.0,0.0],"ents":[["A",[0.0842,0.0002],0.027,90.0,0.0],["A",[0.1382,0.0002],0.027,270.0,180.0],["L",[0.0572,0.0002],[0.016,0.0002]],["L",[0.1915,-0.026],[0.1652,0.0003]],["L",[0.2178,0.0003],[0.1915,0.0266]],["L",[0.1915,-0.026],[0.2178,0.0003]],["L",[0.1652,0.0003],[0.1915,0.0266]]],"bbox":[0.016,0.218,-0.027,0.027]},"Occpncy-Sensor":{"base":[0.0,0.0],"ents":[["C",[0.0,0.0],0.0891],["T","OS",[-0.0811,-0.0396],0.075,0]],"bbox":[-0.089,0.089,-0.089,0.089]},"WIRE":{"base":[0.0,0.0],"ents":[["L",[0.0467,0.1067],[0.1001,0.0695]],["L",[-0.0467,0.0533],[-0.0467,-0.0533]],["L",[0.0467,0.1067],[0.0467,-0.1067]],["L",[0.0,0.1067],[0.0,-0.1067]]],"bbox":[-0.047,0.1,-0.107,0.107]},"AHEAD":{"base":[0.0,0.0],"ents":[["S",[[0.0,0.0377],[0.0,-0.0377],[0.1819,0.0],[0.1819,0.0]]],["L",[0.1819,0.0],[0.0,-0.0377]],["L",[0.0,0.0377],[0.1819,0.0]],["L",[0.0,0.0377],[0.0,-0.0377]]],"bbox":[0.0,0.182,-0.038,0.038]},"Cndt-s":{"base":[0.0,0.0],"ents":[["A",[-0.0327,-0.0296],0.0442,42.14,137.86],["A",[0.0327,0.0296],0.0442,222.14,317.86]],"bbox":[-0.077,0.077,-0.074,0.074]},"Tap":{"base":[0.0,0.0],"ents":[["P",[[-0.0169,0.0,1.0],[0.0169,0.0,1.0]],true]],"bbox":[-0.017,0.017,0.0,0.0]},"Light-1a":{"base":[0.0,0.0],"ents":[["C",[0.0,0.0],0.088]],"bbox":[-0.088,0.088,-0.088,0.088]},"Rcpt":{"base":[0.0,0.0],"ents":[["L",[0.0264,0.0045],[0.253,0.0045]],["L",[0.0264,0.0519],[0.253,0.0519]],["C",[0.1867,0.0282],0.0704]],"bbox":[0.026,0.257,-0.042,0.099]},"Rcpt-gf":{"base":[0.0,0.0],"ents":[["L",[0.025,0.0038],[0.025,0.0511]],["S",[[0.025,0.0511],[0.025,0.0038],[0.2516,0.0511],[0.2516,0.0038]]],["S",[[0.2516,0.0038],[0.2493,0.0285],[0.2557,0.0278],[0.2557,0.0278]]],["L",[0.025,0.0038],[0.2516,0.0038]],["L",[0.025,0.0511],[0.2516,0.0511]],["S",[[0.2516,0.0511],[0.2491,0.0266],[0.2557,0.0278],[0.2557,0.0278]]],["C",[0.1854,0.0275],0.0704]],"bbox":[0.025,0.256,-0.043,0.098]},"DATA":{"base":[0.0,0.0],"ents":[["L",[0.118,0.0707],[0.0,0.0]],["L",[0.118,-0.0707],[0.118,0.0707]],["L",[0.0,0.0],[0.118,-0.0707]],["L",[0.0,0.0],[0.118,0.0]],["S",[[0.0,0.0],[0.118,-0.0707],[0.118,0.0],[0.118,0.0]]]],"bbox":[0.0,0.118,-0.071,0.071]}}''')     # firm symbol library, paper inches, insert at sheet scale factor

def ensure_cca_block(doc,name):
    """Create one of the firm's symbol blocks in doc from the embedded library."""
    if name in doc.blocks: return
    spec=CCA_SYMBOLS.get(name)
    if not spec: return
    b=doc.blocks.new(name,base_point=(spec["base"][0],spec["base"][1],0))
    for en in spec["ents"]:
        k=en[0]
        try:
            if k=="L": b.add_line(tuple(en[1]),tuple(en[2]))
            elif k=="C": b.add_circle(tuple(en[1]),en[2])
            elif k=="A": b.add_arc(tuple(en[1]),en[2],en[3],en[4])
            elif k=="P": b.add_lwpolyline([(p[0],p[1],0,0,p[2]) for p in en[1]],format="xyseb",close=en[2])
            elif k=="S": b.add_solid([tuple(p) for p in en[1]])
            elif k=="T": b.add_text(en[1],dxfattribs={"insert":tuple(en[2]),"height":en[3],"rotation":en[4]})
        except Exception: pass

def room_model(plan_doc, u):
    """Walls (grid-indexed), door arcs, room labels, and the tests built on them.
       Shared by the lighting and power passes."""
    import math as _m
    pm=plan_doc.modelspace()
    # ── walls (with a grid index) ──
    walls=[]
    for e in pm:
        try:
            lay=e.dxf.layer.upper()
            if not any(k in lay for k in ("WALL","MURO","PARED","GLAZ","CURT")): continue
            if e.dxftype()=="LINE": walls.append((e.dxf.start.x,e.dxf.start.y,e.dxf.end.x,e.dxf.end.y))
            elif e.dxftype()=="LWPOLYLINE":
                pts=[(p[0],p[1]) for p in e.get_points()]
                for a,b in zip(pts,pts[1:]): walls.append((a[0],a[1],b[0],b[1]))
        except Exception: pass
    # closed door leaves count as walls for the room test (an open doorway is a gap in the wall lines)
    door_arcs=[]
    for e in pm:
        try:
            if e.dxftype()=="ARC" and ("DOOR" in e.dxf.layer.upper() or "PUERTA" in e.dxf.layer.upper()):
                r=e.dxf.radius; sw=(e.dxf.end_angle-e.dxf.start_angle)%360
                if 22*u<=r<=60*u and 60<=sw<=120: door_arcs.append(e)
        except Exception: pass
    cell=120.0*u; grid={}
    def _cells(x1,y1,x2,y2):
        for cx in range(int(min(x1,x2)//cell),int(max(x1,x2)//cell)+1):
            for cy in range(int(min(y1,y2)//cell),int(max(y1,y2)//cell)+1): yield (cx,cy)
    nwall=len(walls)
    for e in door_arcs:   # both leaf positions, so the opening is closed whichever way the arc was drawn
        C=e.dxf.center; r=e.dxf.radius
        for a in (e.dxf.start_angle,e.dxf.end_angle):
            walls.append((C.x,C.y,C.x+r*_m.cos(_m.radians(a)),C.y+r*_m.sin(_m.radians(a))))
    for i,w in enumerate(walls):
        for k in _cells(*w): grid.setdefault(k,[]).append(i)
    def _ccw(a,b,c): return (c[1]-a[1])*(b[0]-a[0])>(b[1]-a[1])*(c[0]-a[0])
    def _inter(p,q,w):
        a=(w[0],w[1]); b=(w[2],w[3])
        return _ccw(p,a,b)!=_ccw(q,a,b) and _ccw(p,q,a)!=_ccw(p,q,b)
    def crossings(p,q):
        seen=set(); n=0
        for k in _cells(p[0],p[1],q[0],q[1]):
            for i in grid.get(k,()):
                if i in seen: continue
                seen.add(i)
                if _inter(p,q,walls[i]): n+=1
        return n
    def dist_to_walls(p):
        best=1e18
        for k in _cells(p[0]-cell,p[1]-cell,p[0]+cell,p[1]+cell):
            for i in grid.get(k,()):
                if i>=nwall: continue
                x1,y1,x2,y2=walls[i]; dx,dy=x2-x1,y2-y1; L2=dx*dx+dy*dy or 1e-9
                t=max(0,min(1,((p[0]-x1)*dx+(p[1]-y1)*dy)/L2)); px,py=x1+t*dx,y1+t*dy
                best=min(best,_m.hypot(p[0]-px,p[1]-py))
        return best
    # ── rooms (labels) ──
    raw=[]
    for e in pm:
        if e.dxftype()=="TEXT" and e.dxf.layer=="A-AREA-IDEN":
            raw.append([e.dxf.text.replace("%%U","").strip(),e.dxf.insert.x,e.dxf.insert.y,e.dxf.height or 6*u])
    # a room name the architect broke across lines ("HIS WALK" / "IN CLOSET") is one label
    raw.sort(key=lambda r:(-r[2],r[1])); used=[False]*len(raw); rooms=[]
    for i,a in enumerate(raw):
        if used[i]: continue
        name,x,y,h=a; used[i]=True; cnt=1
        for j,b in enumerate(raw):
            if used[j]: continue
            if abs(b[1]-x)<max(6*h,30*u) and 0<y-b[2]<2.2*h:
                name+=" "+b[0]; used[j]=True; x=(x*cnt+b[1])/(cnt+1); y=(y*cnt+b[2])/(cnt+1); cnt+=1
        rooms.append((name,(x,y)))
    R=600.0*u
    def room_of(p):
        cand=sorted(((_m.hypot(p[0]-c[0],p[1]-c[1]),i) for i,(n,c) in enumerate(rooms)),key=lambda t:t[0])[:6]
        best=None
        for d,i in cand:
            if d>R: break
            x=crossings(p,rooms[i][1])
            key=(x,d)
            if best is None or key<best[0]: best=(key,i)
            if x==0: break
        return best[1] if best and best[0][0]<=2 else None
    # ── doors → switch spots ──
    doors=[]
    for e in door_arcs:
        try:
            r=e.dxf.radius; sw=(e.dxf.end_angle-e.dxf.start_angle)%360
            C=(e.dxf.center.x,e.dxf.center.y)
            P1=(C[0]+r*_m.cos(_m.radians(e.dxf.start_angle)),C[1]+r*_m.sin(_m.radians(e.dxf.start_angle)))
            P2=(C[0]+r*_m.cos(_m.radians(e.dxf.end_angle)),C[1]+r*_m.sin(_m.radians(e.dxf.end_angle)))
            mid=_m.radians((e.dxf.start_angle+sw/2)); M=(C[0]+r*_m.cos(mid),C[1]+r*_m.sin(mid))
            rm_=room_of(M)
            if rm_ is None: continue
            Pc,Po=(P1,P2) if dist_to_walls(P1)<=dist_to_walls(P2) else (P2,P1)   # closed leaf lies on the wall
            u1=((Pc[0]-C[0])/r,(Pc[1]-C[1])/r); u2=((Po[0]-C[0])/r,(Po[1]-C[1])/r)
            wall_pt=(C[0]+(r+8*u)*u1[0],C[1]+(r+8*u)*u1[1])          # strike side, 8" past the jamb
            doors.append(dict(room=rm_,wall=wall_pt,u1=u1,u2=u2,r=r))
        except Exception: pass
    return dict(walls=walls,nwall=nwall,door_arcs=door_arcs,crossings=crossings,
                dist_to_walls=dist_to_walls,rooms=rooms,room_of=room_of,doors=doors,cells=_cells,grid=grid)

def lighting_pass(E, plan_doc, rcp_scratch, S, u):
    """Design the lighting plan on the LIGHTING copy (offset 0,0):
       rooms from the architect's labels (wall-crossing aware), one switching
       group per fixture type per room, switches at the door latch side,
       lowercase switching letters, curved conduit runs switch→fixtures.
       S = model units per paper inch (symbol size), u = model units per real inch."""
    import math as _m
    msp=E.modelspace()
    for ln,col in (("E-SWITCH",4),("E-Wiring",4),("E-LITE-EQPM-IDEN",33)):
        if ln not in E.layers: E.layers.new(ln).color=col
    RM=room_model(plan_doc,u)
    rooms=RM["rooms"]; room_of=RM["room_of"]; doors=RM["doors"]
    # ── fixtures ──
    fx=[]
    for e in rcp_scratch.modelspace():
        if e.dxftype()=="INSERT" and e.dxf.layer=="E-LITE-EQPM":
            t=e.dxf.name.split(" - ")[0].strip(); t=re.sub(r"[-_ ]?\d{6,}.*$","",t)[:12]
            fx.append(dict(p=(e.dxf.insert.x,e.dxf.insert.y),type=t))
    for f in fx: f["room"]=room_of(f["p"])
    # ── groups & placement ──
    by_room={}
    for i,f in enumerate(fx):
        if f["room"] is None: continue
        by_room.setdefault(f["room"],{}).setdefault(f["type"],[]).append(i)
    MAXLEG=10
    def snake(idx):
        """Order fixtures row by row, alternating direction, then cut into legs."""
        if len(idx)<=1: return [idx]
        ys=sorted(fx[i]["p"][1] for i in idx)
        gaps=sorted(b-a for a,b in zip(ys,ys[1:]) if b-a>6*u)
        band=(gaps[len(gaps)//2]*0.6) if gaps else 48*u
        rows={}
        for i in idx: rows.setdefault(round(fx[i]["p"][1]/max(band,1e-9)),[]).append(i)
        ordered=[]
        for r_,(k,row) in enumerate(sorted(rows.items(),key=lambda t:-t[0])):
            row.sort(key=lambda i:fx[i]["p"][0],reverse=(r_%2==1)); ordered+=row
        return [ordered[k:k+MAXLEG] for k in range(0,len(ordered),MAXLEG)]
    for rm_ in list(by_room):
        legs={}
        for typ,idx in sorted(by_room[rm_].items(),key=lambda kv:-len(kv[1])):
            for n_,leg in enumerate(snake(idx)): legs[f"{typ}#{n_}"]=leg
        by_room[rm_]=legs
    letters="abcdefghijklmnopqrstuvwxyz"
    nsw=0; nrun=0
    for ensure in ("Switch","Switch-3W"): ensure_cca_block(E,ensure)
    for rm_,groups in by_room.items():
        rd=[d for d in doors if d["room"]==rm_]
        order=sorted(groups.items(),key=lambda kv:-len(kv[1]))
        for gi,(typ,idx) in enumerate(order):
            L=letters[gi%26]
            for i in idx:
                p=fx[i]["p"]
                msp.add_text(L,dxfattribs={"layer":"E-LITE-EQPM-IDEN","color":256,"height":0.0625*S,
                                           "insert":(p[0]+0.12*S,p[1]-0.18*S)})
        if not rd: continue
        rd=sorted(rd,key=lambda d:-d["r"])[:3]
        blk="Switch" if len(rd)==1 else "Switch-3W"
        for di,d in enumerate(rd):
            ang=_m.degrees(_m.atan2(d["u2"][1],d["u2"][0]))
            for gi,(typ,idx) in enumerate(order):
                sp=(d["wall"][0]+gi*0.2*S*d["u1"][0],d["wall"][1]+gi*0.2*S*d["u1"][1])
                msp.add_blockref(blk,sp,dxfattribs={"layer":"E-SWITCH","color":256,"rotation":ang,"xscale":S,"yscale":S})
                msp.add_text(letters[gi%26],dxfattribs={"layer":"E-SWITCH","color":256,"height":0.05*S,
                             "insert":(sp[0]+0.19*S*d["u2"][0]+0.04*S*d["u1"][0],sp[1]+0.19*S*d["u2"][1]+0.04*S*d["u1"][1])})
                nsw+=1
                if di==0:   # conduit run: switch → nearest end of the leg, then along the leg in row order
                    start=(sp[0]+0.1*S*d["u2"][0],sp[1]+0.1*S*d["u2"][1])
                    seq=list(idx)
                    if _m.hypot(fx[seq[-1]]["p"][0]-start[0],fx[seq[-1]]["p"][1]-start[1])<_m.hypot(fx[seq[0]]["p"][0]-start[0],fx[seq[0]]["p"][1]-start[1]): seq.reverse()
                    cur=start
                    for j in seq:
                        q=fx[j]["p"]
                        msp.add_lwpolyline([(cur[0],cur[1],0,0,0.25),(q[0],q[1],0,0,0)],format="xyseb",
                                           dxfattribs={"layer":"E-Wiring","color":256})
                        nrun+=1; cur=q
    unassigned=sum(1 for f in fx if f["room"] is None)
    return dict(fixtures=len(fx),rooms=len(by_room),switches=nsw,runs=nrun,unassigned=unassigned,doors=len(doors))

def power_pass(E, plan_doc, S, u, ox):
    """Receptacles on the POWER copy (offset ox). Dwelling rooms follow NEC
       210.52(A): no point along a wall more than 6 ft from a receptacle, walls
       under 2 ft skipped. Wet/outdoor rooms get GFCI. Halls get one per long
       wall. Open commercial areas get perimeter receptacles every 20 ft.
       Symbols are the firm's Rcpt / Rcpt-gf blocks at the sheet scale."""
    import math as _m
    msp=E.modelspace()
    if "E-EQUIP" not in E.layers: E.layers.new("E-EQUIP").color=4
    for b in ("Rcpt","Rcpt-gf"): ensure_cca_block(E,b)
    RM=room_model(plan_doc,u)
    walls=RM["walls"][:RM["nwall"]]; rooms=RM["rooms"]; room_of=RM["room_of"]; doors=RM["doors"]
    GFCI=("BATH","TOILET","RESTROOM","W.C","LAVAT","POWDER","SHOWER","KITCHEN","COCINA","PANTRY",
          "LAUNDRY","LAVAND","GARAGE","GARAJE","TERRACE","PATIO","BALCONY","POOL","BBQ","EXTERIOR",
          "OUTDOOR","DECK","CARPORT","JANITOR","MECH","UTILITY","SINK","BAR","BREAK","WET")
    NONE=("CLOSET","CLO.","W.I.C","WIC","STAIR","ELEV","SHAFT","ATTIC","CRAWL","PARKING","DRIVEWAY",
          "LOT","GREEN","PLANTER","POND","ROOF","PROPERTY","STO.","STORAGE","CHASE","DUCT",
          "SHOWER","BATHTUB","TUB","WET AREA","CISTERN","GENERATOR")
    HALL=("HALL","CORRIDOR","FOYER","VESTIBULE","LOBBY","PASSAGE")
    OPEN=("SHOWROOM","SALES","SERVICE","SHOP","WAREHOUSE","WAITING","LOUNGE","DISPLAY","BAY","AREA")
    BATH=("BATH","TOILET","RESTROOM","W.C","LAVAT","POWDER","R.R")
    OUTDOOR=("TERRACE","PATIO","BALCONY","DECK","PORCH","GARDEN","YARD","POOL","BBQ","EXTERIOR","OUTDOOR","CARPORT")
    UTIL=("LAUNDRY","LAVAND","GARAGE","GARAJE","MECH","UTILITY","JANITOR","ELECT","PANTRY")
    def category(name):
        n=name.upper()
        if any(k in n for k in NONE): return "none"
        if any(k in n for k in BATH): return "bath"
        if any(k in n for k in OUTDOOR): return "outdoor"
        if any(k in n for k in UTIL): return "util"
        if any(k in n for k in HALL): return "hall"
        if any(k in n for k in OPEN): return "open"
        return "room"
    def is_gfci(name): return any(k in name.upper() for k in GFCI)
    # points to keep clear: door hinge and strike
    avoid=[]
    for d in doors:
        w=d["wall"]; r=d["r"]; u1=d["u1"]
        avoid.append(w); avoid.append((w[0]-(r+8*u)*u1[0],w[1]-(r+8*u)*u1[1]))
    def near_avoid(p,tol):
        return any(_m.hypot(p[0]-a[0],p[1]-a[1])<tol for a in avoid)
    placed=[]; per_room={}
    LIMIT={"bath":1,"outdoor":1,"util":2}
    # for limited categories, rank each room's facing walls by length and keep the longest
    facing={}
    for i,(x1,y1,x2,y2) in enumerate(walls):
        L=_m.hypot(x2-x1,y2-y1)
        if L<24*u: continue
        dx,dy=(x2-x1)/L,(y2-y1)/L; n=(-dy,dx); mid=((x1+x2)/2,(y1+y2)/2)
        for side in (1,-1):
            rid=room_of((mid[0]+n[0]*side*8*u,mid[1]+n[1]*side*8*u))
            if rid is not None: facing.setdefault(rid,[]).append((L,i,side))
    allowed_walls={}
    for rid,lst in facing.items():
        cat=category(rooms[rid][0])
        if cat in LIMIT:
            lst.sort(reverse=True); allowed_walls[rid]={(i,s) for _,i,s in lst[:LIMIT[cat]]}
    def put(p,n,gf,rid):
        if any(_m.hypot(p[0]-q[0],p[1]-q[1])<18*u for q in placed): return False
        ang=_m.degrees(_m.atan2(n[1],n[0]))
        msp.add_blockref("Rcpt-gf" if gf else "Rcpt",(p[0]+ox,p[1]),
                         dxfattribs={"layer":"E-EQUIP","color":256,"rotation":ang,"xscale":S,"yscale":S})
        placed.append(p); per_room[rid]=per_room.get(rid,0)+1; return True
    for i,(x1,y1,x2,y2) in enumerate(walls):
        L=_m.hypot(x2-x1,y2-y1)
        if L<24*u: continue
        dx,dy=(x2-x1)/L,(y2-y1)/L; n=(-dy,dx); mid=((x1+x2)/2,(y1+y2)/2)
        for side in (1,-1):
            nin=(n[0]*side,n[1]*side)
            probe=(mid[0]+nin[0]*8*u,mid[1]+nin[1]*8*u)
            rid=room_of(probe)
            if rid is None: continue
            name=rooms[rid][0]; cat=category(name)
            if cat=="none": continue
            gf=is_gfci(name) or cat in ("bath","outdoor")
            if cat in LIMIT:
                if (i,side) not in allowed_walls.get(rid,()): continue
                ts=[L/2]
            elif cat=="hall":
                ts=[L/2] if L>=120*u else []
            elif cat=="open":
                first,step=min(120*u,L/2),240*u
                ts=[]; t=first
                while t<=L-min(120*u,L/2)+1e-6: ts.append(t); t+=step
                if not ts: ts=[L/2]
            else:
                first,step=72*u,144*u
                if L<=144*u: ts=[L/2]
                else:
                    ts=[]; t=first
                    while t<L-72*u+1e-6: ts.append(t); t+=step
                    if L-ts[-1]>72*u: ts.append(L-72*u)
            for t in ts:
                p=(x1+dx*t+nin[0]*1.5*u, y1+dy*t+nin[1]*1.5*u)
                if near_avoid(p,16*u): continue
                put(p,nin,gf,rid)
    return dict(receptacles=len(placed),rooms=len(per_room))

# ═══════════════════════════════════════════════════════════════════════════
# PREVIEWS — PNG renders so nothing has to be opened in AutoCAD to check it
# ═══════════════════════════════════════════════════════════════════════════
_FONTS_READY=[False]
def _ensure_fonts():
    """python:*-slim ships no fonts, so ezdxf would silently skip all text.
    Feed it the TrueType fonts bundled inside matplotlib instead."""
    if _FONTS_READY[0]: return
    try:
        import matplotlib
        from ezdxf.fonts import fonts as _f
        mpl_fonts=os.path.join(os.path.dirname(matplotlib.__file__),"mpl-data","fonts","ttf")
        if not _f.font_manager.has_font("DejaVuSans.ttf"):
            _f.font_manager.build([mpl_fonts])
    except Exception: pass
    _FONTS_READY[0]=True

def render_svgs(doc, views, black=True):
    """views: [(name, (x1,x2,y1,y2) | None)] → {name: svg string}. Vector output:
    crisp at any zoom, text as glyph outlines. Each view only draws what it shows."""
    _ensure_fonts()
    from ezdxf.addons.drawing import Frontend, RenderContext, layout as dl
    from ezdxf.addons.drawing.svg import SVGBackend
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy
    cfg=Configuration(background_policy=BackgroundPolicy.WHITE,min_lineweight=0.1,
                      color_policy=ColorPolicy.BLACK if black else ColorPolicy.COLOR)
    msp=doc.modelspace(); out={}
    for name,box in views:
        if box:
            px=0.03*(box[1]-box[0]); py=0.03*(box[3]-box[2])
            X1,X2,Y1,Y2=box[0]-px,box[1]+px,box[2]-py,box[3]+py
            def keep(e,X1=X1,X2=X2,Y1=Y1,Y2=Y2):
                try:
                    t=e.dxftype()
                    if t in ("TEXT","MTEXT","INSERT"):
                        p=e.dxf.insert; return X1<=p.x<=X2 and Y1<=p.y<=Y2
                    g=leaf_geom(e,1.0)
                    if g is None: return True
                    b=geom_bbox(g); return b[0]<=X2 and b[1]>=X1 and b[2]<=Y2 and b[3]>=Y1
                except Exception: return True
        else: keep=None
        ctx=RenderContext(doc); ctx.set_current_layout(msp)
        be=SVGBackend()
        Frontend(ctx,be,config=cfg).draw_layout(msp,finalize=True,filter_func=keep)
        svg=be.get_string(dl.Page(0,0),settings=dl.Settings(fit_page=True))
        out[name]=svg
    return out

_VIEWER_HTML='''<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#fff;overflow:hidden;font-family:sans-serif}
#wrap{position:absolute;inset:0;cursor:grab}#wrap:active{cursor:grabbing}
#wrap svg{width:100%;height:100%;display:block}
#hint{position:absolute;right:10px;top:8px;font-size:12px;color:#888;background:#fff;padding:2px 6px;border-radius:4px;pointer-events:none}
</style></head><body><div id="wrap">__SVG__</div><div id="hint">scroll = zoom · drag = pan · double-click = reset</div>
<script>
const wrap=document.getElementById('wrap'),svg=wrap.querySelector('svg');
svg.removeAttribute('width');svg.removeAttribute('height');svg.setAttribute('preserveAspectRatio','xMidYMid meet');
const orig=svg.getAttribute('viewBox').trim().split(/[ ,]+/).map(Number);let vb=orig.slice();
const apply=()=>svg.setAttribute('viewBox',vb.join(' '));
wrap.addEventListener('wheel',e=>{e.preventDefault();const r=svg.getBoundingClientRect();
 const sx=Math.max(r.width/orig[2],r.height/orig[3]);const cw=orig[2]*sx,ch=orig[3]*sx; // rendered content box (meet)
 const ox=r.left+(r.width-cw)/2,oy=r.top+(r.height-ch)/2;
 const fx=(e.clientX-ox)/cw,fy=(e.clientY-oy)/ch;const k=e.deltaY<0?0.8:1.25;
 const mx=vb[0]+fx*vb[2],my=vb[1]+fy*vb[3];vb[2]*=k;vb[3]*=k;vb[0]=mx-fx*vb[2];vb[1]=my-fy*vb[3];apply();},{passive:false});
let drag=null;
wrap.addEventListener('mousedown',e=>{drag=[e.clientX,e.clientY,vb[0],vb[1]];e.preventDefault();});
window.addEventListener('mousemove',e=>{if(!drag)return;const r=svg.getBoundingClientRect();
 const sx=Math.min(r.width/vb[2],r.height/vb[3]);vb[0]=drag[2]-(e.clientX-drag[0])/sx;vb[1]=drag[3]-(e.clientY-drag[1])/sx;apply();});
window.addEventListener('mouseup',()=>drag=null);
wrap.addEventListener('dblclick',()=>{vb=orig.slice();apply();});
</script></body></html>'''
def viewer_html(svg): return _VIEWER_HTML.replace("__SVG__",svg)

def build_previews(outdir, floors, rcp_scratch, has_rcp, power_ox=None):
    """Floor plans (labels), lighting copy with the RCP, and the E-101 sheet with its frame."""
    previews=[]
    try:
        plan=ezdxf.readfile(str(outdir/"x-plan.dxf"))
        views=[(f"Plan — {n}" if n else "Plan",b) for n,b in floors] if floors else [("Plan",None)]
        for k,v in render_svgs(plan,views).items(): previews.append((k,v))
        # power design: receptacles from E-Electrical's POWER copy, shifted back onto the plan
        if power_ox is not None:
            try:
                from ezdxf.addons import Importer as _Imp
                Ed=ezdxf.readfile(str(next((outdir/n for n in ("E-Electrical Plan.dxf","E-Electrical Plans.dxf") if (outdir/n).exists()),outdir/"E-Electrical Plan.dxf")))
                rc=[e for e in Ed.modelspace() if e.dxftype()=="INSERT" and e.dxf.layer=="E-EQUIP"]
                if rc:
                    pp=ezdxf.readfile(str(outdir/"x-plan.dxf"))
                    _d=_Imp(Ed,pp); _d.import_entities(rc,pp.modelspace()); _d.finalize()
                    for e in list(pp.modelspace())[-len(rc):]:
                        try: e.translate(-power_ox,0,0)
                        except Exception: pass
                    if "E-EQUIP" in pp.layers: pp.layers.get("E-EQUIP").color=4
                    for k,v in render_svgs(pp,[(f"Power design — {n}" if n else "Power design",b) for n,b in floors],black=False).items(): previews.append((k,v))
            except Exception as ex: print("power preview:",ex)
        if has_rcp and rcp_scratch is not None:
            from ezdxf.addons import Importer
            blocks=[e for e in rcp_scratch.modelspace() if e.dxftype() in ("INSERT","TEXT")]
            if blocks:
                try: _i=Importer(rcp_scratch,plan); _i.import_entities(blocks,plan.modelspace()); _i.finalize()
                except Exception: pass
            for e in rcp_scratch.modelspace():
                if e.dxftype() in ("INSERT","TEXT"): continue
                g=leaf_geom(e,0.5)
                if g: write_leaf(plan.modelspace(),g,{"layer":e.dxf.layer,"color":256})
            if "A-CLNG" not in plan.layers: plan.layers.new("A-CLNG").color=9
            if "E-LITE-EQPM" not in plan.layers: plan.layers.new("E-LITE-EQPM").color=33
            for k,v in render_svgs(plan,[(f"Lighting + RCP — {n}" if n else "Lighting + RCP",b) for n,b in floors],black=False).items(): previews.append((k,v))
            # lighting design: switches, letters, conduit runs from E-Electrical's lighting copy (offset 0,0)
            try:
                Ed=ezdxf.readfile(str(next((outdir/n for n in ("E-Electrical Plan.dxf","E-Electrical Plans.dxf") if (outdir/n).exists()),outdir/"E-Electrical Plan.dxf")))
                des=[e for e in Ed.modelspace() if e.dxf.layer in ("E-SWITCH","E-Wiring") or (e.dxftype()=="TEXT" and e.dxf.layer=="E-LITE-EQPM-IDEN" and len(e.dxf.text)==1)]
                if des:
                    _d=Importer(Ed,plan); _d.import_entities(des,plan.modelspace()); _d.finalize()
                    for ln,col in (("E-SWITCH",4),("E-Wiring",4)):
                        if ln in plan.layers: plan.layers.get(ln).color=col
                    for k,v in render_svgs(plan,[(f"Lighting design — {n}" if n else "Lighting design",b) for n,b in floors],black=False).items(): previews.append((k,v))
            except Exception as ex: print("lighting preview:",ex)
    except Exception as ex:
        previews.append(("Plan (render failed)",None)); print("preview:",ex)
    try:
        from ezdxf.addons import Importer
        marco=ezdxf.readfile(str(outdir/"x-marco.dxf")); E=ezdxf.readfile(str(next((outdir/n for n in ("E-Electrical Plan.dxf","E-Electrical Plans.dxf") if (outdir/n).exists()),outdir/"E-Electrical Plan.dxf")))
        lay=next((l for l in E.layouts if l.name.startswith("E-1")),None)
        sh=ezdxf.new("R2018",setup=True); sm=sh.modelspace()
        imp=Importer(marco,sh); imp.import_entities(list(marco.modelspace()),sm); imp.finalize()
        if lay is not None:
            imp2=Importer(E,sh); imp2.import_entities([e for e in lay if e.dxftype()!="VIEWPORT"],sm); imp2.finalize()
            for v in lay:
                if v.dxftype()=="VIEWPORT" and v.dxf.id!=1:
                    cx,cy,w,h=v.dxf.center.x,v.dxf.center.y,v.dxf.width,v.dxf.height
                    sm.add_lwpolyline([(cx-w/2,cy-h/2),(cx+w/2,cy-h/2),(cx+w/2,cy+h/2),(cx-w/2,cy+h/2)],close=True,dxfattribs={"color":4})
                    sm.add_text("VIEWPORT → plan goes here",dxfattribs={"insert":(cx-2.5,cy),"height":0.5,"color":4})
        for k,v in render_svgs(sh,[(f"Sheet {lay.name if lay else ''} — frame + fields",None)],black=False).items(): previews.append((k,v))
    except Exception as ex:
        previews.append(("Sheet (render failed)",None)); print("preview:",ex)
    return previews

# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════
def process_files(uploaded_files):
    import zipfile, io, shutil
    tmp = Path(tempfile.mkdtemp())
    dxf_paths = []; conversion_errors = []
    for f in uploaded_files:
        p = tmp / f.name
        p.write_bytes(f.read())
        if f.name.lower().endswith(".pdf"):
            continue
        if f.name.lower().endswith(".dwg"):
            st.write(f"Converting {f.name}...")
            converted = dwg_to_dxf(p)
            if converted: dxf_paths.append(converted); st.write("✓ Done")
            else: conversion_errors.append(f.name)
        else:
            dxf_paths.append(p)
    if conversion_errors and not dxf_paths:
        return None, None, f"Could not convert: {', '.join(conversion_errors)}"
    if not dxf_paths:
        return None, None, "No readable files found."

    # ── reference-set mode: a finished E-set from the engineer is the template
    pdfs=[tmp/f.name for f in uploaded_files if f.name.lower().endswith(".pdf")]
    ref_path=None
    for p in dxf_paths:
        try:
            if is_reference_set(read_dxf_any(p)): ref_path=p; break
        except Exception: pass
    if ref_path is not None:
        marco_path=next((p for p in dxf_paths if _norm(p.stem).startswith("xmarco")),None)
        others=[p for p in dxf_paths if p not in (ref_path,marco_path)]
        # the template's own clean xref (named in its xref path) is a reference, not the job
        try:
            refdoc=read_dxf_any(ref_path)
            xp=next((b.block.dxf.xref_path for b in refdoc.blocks if (b.block.dxf.flags&4) and _norm(b.name).startswith("xplan")),"")
            xstem=_norm(Path(xp.replace("\\","/")).stem) if xp else ""
            plan_paths=[p for p in others if not (xstem and _norm(p.stem)==xstem)] or others
        except Exception: plan_paths=others
        return process_reference_set(ref_path,plan_paths,pdfs[0] if pdfs else None,marco_path,tmp,[])
    dxf_stems = {p.stem.lower() for p in dxf_paths}
    rcp_paths = [p for p in dxf_paths if is_rcp(p.name)]
    floor_paths = [p for p in dxf_paths if not is_rcp(p.name)] or list(dxf_paths)
    file_meta = [(p,)+quick_scan(p) for p in floor_paths]
    gc.collect()
    candidates=[m for m in file_meta if m[1]] or list(file_meta)
    # every sheet that scores as a floor plan is a plan part (A101 + A102 → A, B)
    top=max(sheet_score(m[0].name) for m in candidates)
    plan_sheets=[m[0] for m in candidates if sheet_score(m[0].name)==top and m[2]>=15] or \
                [max(candidates,key=lambda m:sheet_score(m[0].name))[0]]
    plan_sheets=sorted(plan_sheets,key=lambda p:p.name)[:6]

    parts=[]; masters=set(); debug=[]
    for sp in plan_sheets:
        part=extract_plan(sp,dxf_paths,dxf_stems)
        parts.append(part); debug+=part["debug"]
        if part["master_path"]: masters.add(part["master_path"])
    ins_units=parts[0]["ins_units"]

    # content-based RCP detection (files with no telling name)
    sheet_ratio=0.0
    for m in file_meta:
        if m[0] in plan_sheets and m[5]: sheet_ratio=max(sheet_ratio,m[4]/m[5])
    for (p,vp,w,x,ceil,total,rtxt) in file_meta:
        if p in plan_sheets or p in masters or p in rcp_paths or sheet_score(p.name)==0: continue
        ratio=(ceil/total) if total else 0.0
        if (ceil>=20 and ratio>=0.10 and ratio>=2*sheet_ratio) or (rtxt and ceil>=10 and ratio>=0.03):
            rcp_paths.append(p)

    # union of all parts' zones + floor fingerprint for the RCP
    all_zones=[z for pt in parts for z in pt["zones"]]
    union=(min(z[0] for z in all_zones),max(z[1] for z in all_zones),
           min(z[2] for z in all_zones),max(z[3] for z in all_zones))
    MG=50; floor_keys=set(); fw=[]
    for pt in parts:
        for e in pt["doc"].modelspace():
            try:
                if e.dxftype()=="LINE":
                    mx=(e.dxf.start.x+e.dxf.end.x)/2; my=(e.dxf.start.y+e.dxf.end.y)/2
                    floor_keys.add((round(mx/MG),round(my/MG)))
                    if e.dxf.layer=="A-WALL": fw.append((mx,my))
                elif e.dxftype()=="LWPOLYLINE":
                    pts=list(e.get_points())
                    for a,b in zip(pts,pts[1:]): floor_keys.add((round((a[0]+b[0])/2/MG),round((a[1]+b[1])/2/MG)))
            except: pass
    fbox=(min(p[0] for p in fw),max(p[0] for p in fw),min(p[1] for p in fw),max(p[1] for p in fw)) if len(fw)>=20 else union
    rcp_scratch=None; rcp_diag=[]
    for rp in rcp_paths:
        try:
            sc,dg=extract_rcp(rp,dxf_paths,dxf_stems,all_zones,floor_keys,fbox,parts[0]["flat"])
            rcp_diag.append(dg)
            if rcp_scratch is None: rcp_scratch=sc
            else:
                for e in sc.modelspace():
                    g=leaf_geom(e,0.5)
                    if g: write_leaf(rcp_scratch.modelspace(),g,{"layer":e.dxf.layer,"color":256})
        except Exception as ex:
            rcp_diag.append(f"{rp.name} [ERROR: {ex}]")

    # marco from the first plan sheet
    try:
        marco,minfo=extract_marco(plan_sheets[0])
    except Exception as ex:
        marco,minfo=ezdxf.new("R2000"),_default_info()
        debug.append(f"marco error: {ex}")

    # x-plan / x-planA,B…
    stems=[p.stem for p in dxf_paths]
    pre=os.path.commonprefix(stems).strip(" -_")
    project=pre if len(pre)>=4 else max(stems,key=len)
    outdir=tmp/"out"; outdir.mkdir()
    files={}
    def plan_letter(part,i):
        mt=re.search(r"PLAN[ _-]?([A-Z])\b",part["sheet_path"].stem.upper())
        return mt.group(1) if mt else chr(65+i)
    # Always ONE x-plan: when the architect split the building across sheets,
    # the parts share coordinates, so they merge into the same model space.
    split=[]
    base=parts[0]["doc"]; bm=base.modelspace()
    for pt in parts[1:]:
        for e in pt["doc"].modelspace():
            try:
                if e.dxftype()=="TEXT":
                    bm.add_text(e.dxf.text,dxfattribs={"layer":e.dxf.layer,"color":256,"insert":(e.dxf.insert.x,e.dxf.insert.y),
                                                       "height":e.dxf.height,"rotation":e.dxf.rotation})
                else:
                    g=leaf_geom(e,parts[0]["flat"])
                    if g: write_leaf(bm,g,{"layer":e.dxf.layer,"color":256})
            except: pass
    files["x-plan.dxf"]=base
    files["x-marco.dxf"]=marco
    # floors: every zone across parts; overlapping zones (split halves) are one floor
    named=[(z,nm) for pt in parts for z,nm in zip(pt["zones"],pt.get("zone_names",[""]*len(pt["zones"])))]
    floor_zones=[]   # [(zone, [names])]
    for z,nm in named:
        for i,(z2,nms) in enumerate(floor_zones):
            ix=max(0,min(z[1],z2[1])-max(z[0],z2[0])); iy=max(0,min(z[3],z2[3])-max(z[2],z2[2]))
            a1=(z[1]-z[0])*(z[3]-z[2]); a2=(z2[1]-z2[0])*(z2[3]-z2[2])
            if ix*iy>0.2*min(a1,a2):
                floor_zones[i]=((min(z[0],z2[0]),max(z[1],z2[1]),min(z[2],z2[2]),max(z[3],z2[3])),nms+([nm] if nm else [])); break
        else: floor_zones.append((z,[nm] if nm else []))
    def _nat(s): return [0 if s[1] else 1]+[int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)"," ".join(s[1]))]
    floor_zones.sort(key=_nat)
    if all(not nms for _,nms in floor_zones) and len(floor_zones)>1:
        _GK=("KITCHEN","COCINA","LIVING","SALA","GARAGE","CARPORT","ENTRANCE","FOYER","DINING","COMEDOR","MARQUESINA")
        def _gscore(z):
            n=0
            for pt in parts:
                for e in pt["doc"].modelspace():
                    if e.dxftype()=="TEXT" and e.dxf.layer=="A-AREA-IDEN" and z[0]<=e.dxf.insert.x<=z[1] and z[2]<=e.dxf.insert.y<=z[3]:
                        if any(k in e.dxf.text.upper() for k in _GK): n+=1
            return -n
        floor_zones.sort(key=lambda t:(_gscore(t[0]),t[0][0]))
    floors=[]
    for i,(z,nms) in enumerate(floor_zones):
        pts=[p for p in fw if z[0]<=p[0]<=z[1] and z[2]<=p[1]<=z[3]]
        b=(min(p[0] for p in pts),max(p[0] for p in pts),min(p[1] for p in pts),max(p[1] for p in pts)) if len(pts)>=20 else z
        name="" if len(floor_zones)==1 else (f"PLAN {'/'.join(dict.fromkeys(nms))}" if nms else f"LEVEL {i+1}")
        floors.append((name,b))
    E,made,scale_label,F=build_electrical(union,rcp_scratch,minfo,ins_units,project,split,
                                        fbox=fbox if len(fw)>=20 else None, floors=floors)
    lighting=None; power=None
    if rcp_scratch is not None:
        try: lighting=lighting_pass(E,base,rcp_scratch,F*UNIT_PER_IN.get(ins_units,1.0),UNIT_PER_IN.get(ins_units,1.0))
        except Exception as ex: debug.append(f"lighting error: {ex}")
    try:
        xs=sorted(e.dxf.insert.x for e in E.modelspace() if e.dxftype()=="INSERT" and e.dxf.name=="x-plan")
        pox=xs[1] if len(xs)>1 else 0.0
        power=power_pass(E,base,F*UNIT_PER_IN.get(ins_units,1.0),UNIT_PER_IN.get(ins_units,1.0),pox)
        power["ox"]=pox
    except Exception as ex: debug.append(f"power error: {ex}")
    files["E-Electrical Plan.dxf"]=E

    # write, convert, zip
    dwg_ok=[]; dwg_fail=[]
    for name,doc in files.items():
        p=outdir/name; doc.saveas(str(p))
        d=dxf_to_dwg(p)
        (dwg_ok if d else dwg_fail).append(name)
    try: previews=build_previews(outdir,floors,rcp_scratch,rcp_scratch is not None,power_ox=(power or {}).get("ox"))
    except Exception: previews=[]
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
        for p in sorted(outdir.iterdir()): z.write(p,p.name)
    shutil.rmtree(tmp,ignore_errors=True)

    msg=(f"Done. {len(parts)} plan part(s), {len(floors)} floor(s) → {', '.join(k for k in files)}; "
         f"{sum(pt['ents'] for pt in parts)} entities, {sum(pt['labels'] for pt in parts)} labels; "
         f"sheets {', '.join(made)} at {scale_label}")
    msg+=f"; marco {'extracted from '+plan_sheets[0].name if minfo.get('found') else 'NOT found (generic frame)'}"
    if rcp_diag: msg+="; RCP "+" | ".join(rcp_diag)
    if lighting: msg+=f"; LIGHTING {lighting['fixtures']} fixtures in {lighting['rooms']} rooms ({lighting['unassigned']} unassigned), {lighting['doors']} doors, {lighting['switches']} switches, {lighting['runs']} runs"
    if power: msg+=f"; POWER {power['receptacles']} receptacles in {power['rooms']} rooms"
    if dwg_fail: msg+=f"; DWG conversion failed for {', '.join(dwg_fail)} (DXF included)"
    if debug: msg+=" | "+" | ".join(debug)
    return (buf.getvalue(),previews), msg+".", None

# ═══════════════════════════════════════════════════════════════════════════
# REFERENCE-SET MODE: a finished E-Electrical set from the same engineer is the
# template. Its E-001 / E-601 sheets are carried over as-is, its E-101 sheet is
# cloned once per floor, its symbol blocks are used for everything we place,
# and its title block is rebuilt from the plotted PDF when x-marco is missing.
# ═══════════════════════════════════════════════════════════════════════════
def fix_dxf_text(path):
    """libredwg can put a raw newline inside a long text value; re-pair the file."""
    try:
        raw=open(path,'rb').read().decode('utf-8','replace').replace('\r\n','\n').split('\n')
        out=[]; i=0; n=len(raw); bad=0
        while i<n:
            code=raw[i]
            try: int(code.strip())
            except ValueError:
                if out: out[-1]+=" "+code.strip()
                i+=1; bad+=1; continue
            out.append(code)
            if i+1<n: out.append(raw[i+1])
            i+=2
        if bad: open(path,'w',encoding='utf-8').write('\n'.join(out))
        return bad
    except Exception: return 0

def read_dxf_any(path):
    """Plain load → text repair → recover loader."""
    from ezdxf import recover
    try: return ezdxf.readfile(str(path))
    except Exception: pass
    fix_dxf_text(str(path))
    try: return ezdxf.readfile(str(path))
    except Exception: pass
    doc,_=recover.readfile(str(path)); return doc

def is_reference_set(doc):
    lays=[l.name for l in doc.layouts if re.match(r"^E-\d{3}$",l.name)]
    xp=any((b.block.dxf.flags & 4) and _norm(b.name).startswith("xplan") for b in doc.blocks)
    return len(lays)>=2 and xp

def load_reference_set(ref):
    """Parse the finished set: layouts, their viewports and fields, where the
       x-plan xref sits in model space, and which blocks are the firm's symbols."""
    R=dict(doc=ref,layouts=[],xplan_inserts=[],marco_name=None)
    m=ref.modelspace()
    for b in ref.blocks:
        if b.block.dxf.flags & 4:
            if _norm(b.name).startswith("xplan"): R["xplan_block"]=b.name
            elif _norm(b.name).startswith("xmarco"): R["marco_name"]=b.name
    R["xplan_inserts"]=[(e.dxf.insert.x,e.dxf.insert.y) for e in m if e.dxftype()=="INSERT" and e.dxf.name==R.get("xplan_block")]
    for lay in ref.layouts:
        if not re.match(r"^E-\d{3}$",lay.name): continue
        L=dict(name=lay.name,layout=lay,vps=[],ents=[],sheetno=None,title=None,vtitles=[],marco_ins=None)
        for e in lay:
            t=e.dxftype()
            if t=="VIEWPORT":
                if e.dxf.id==1 or e.dxf.width<0.5: continue
                L["vps"].append(dict(cx=e.dxf.center.x,cy=e.dxf.center.y,w=e.dxf.width,h=e.dxf.height,
                                     vx=e.dxf.view_center_point.x,vy=e.dxf.view_center_point.y,vh=e.dxf.view_height))
            elif t=="INSERT" and e.dxf.name==R["marco_name"]: L["marco_ins"]=e
            elif t=="TEXT" and e.dxf.text.strip()==lay.name: L["sheetno"]=e
            elif t=="MTEXT" and L["title"] is None and e.dxf.insert.x>30: L["title"]=e
            elif t=="TEXT" and ("%%U" in e.dxf.text or e.dxf.text.strip().upper().startswith("SCALE")) and e.dxf.insert.x<30: L["vtitles"].append(e)
            else: L["ents"].append(e)
        # plan sheets are the 100-series (E-101…E-199); everything else is carried over as-is
        L["kind"]="plan" if re.match(r"^E-1\d\d$",lay.name) and L["vps"] else "static"
        R["layouts"].append(L)
    R["plan_layouts"]=[L for L in R["layouts"] if L["kind"]=="plan"]
    R["static_layouts"]=[L for L in R["layouts"] if L["kind"]=="static"]
    return R

def _copy_layout_setup(src,dst):
    for k in ("paper_width","paper_height","plot_rotation","current_style_sheet","paper_units","plot_paper_units",
              "left_margin","right_margin","top_margin","bottom_margin","plot_origin_x_offset","plot_origin_y_offset",
              "plot_type","plot_paper_size","scale_numerator","scale_denominator","standard_scale_type","limmin","limmax","extmin","extmax"):
        try:
            if src.dxf.hasattr(k): dst.dxf.set(k,src.dxf.get(k))
        except Exception: pass
    try: dst.dxf.plot_layout_flags=src.dxf.plot_layout_flags
    except Exception: pass

def build_from_reference(plan_doc, floors, R, ins_units, floor_names=None):
    """E-Electrical from the template. Returns E, sheet list, scale factor F,
       the two x-plan insert offsets (A: lighting+power, B: comm+smoke) and
       per-floor view windows."""
    from ezdxf.addons import Importer
    ref=R["doc"]; rm=ref.modelspace()
    E=ezdxf.new("R2000",setup=True); msp=E.modelspace()
    imp=Importer(ref,E)
    try: imp.import_tables("*")
    except Exception: pass
    blocks=[b.name for b in ref.blocks if not b.name.startswith("*") and not (b.block.dxf.flags & 4)]
    try: imp.import_blocks(blocks)
    except Exception:
        for bn in blocks:
            try: imp.import_block(bn)
            except Exception: pass
    u=UNIT_PER_IN.get(ins_units,1.0)
    # ── static sheets: model content inside their windows, same coordinates
    def _bbox_of(e):
        try:
            g=leaf_geom(e,1.0)
            if g: return geom_bbox(g)
            if e.dxftype() in ("TEXT","MTEXT","INSERT","DIMENSION","HATCH","SOLID","LEADER","MLEADER"):
                p=e.dxf.get("insert",None) or e.dxf.get("defpoint",None)
                if p is None and e.dxftype()=="SOLID": p=e.dxf.vtx0
                if p is None: return None
                return (p.x,p.x,p.y,p.y)
        except Exception: return None
    windows=[]
    for L in R["static_layouts"]:
        for v in L["vps"]:
            hw=v["vh"]/2*v["w"]/v["h"]; hh=v["vh"]/2
            windows.append((v["vx"]-hw*1.05,v["vx"]+hw*1.05,v["vy"]-hh*1.05,v["vy"]+hh*1.05))
    stat=[]
    for e in rm:
        if e.dxftype()=="INSERT" and e.dxf.name==R.get("xplan_block"): continue
        b=_bbox_of(e)
        if b is None: continue
        if any(b[0]<=W[1] and b[1]>=W[0] and b[2]<=W[3] and b[3]>=W[2] for W in windows): stat.append(e)
    if stat:
        try: imp.import_entities(stat,msp)
        except Exception: pass
    imp.finalize()
    # ── plan placement: keep the architect's coordinates for copy A unless they
    # collide with the carried-over sheets; copy B sits below copy A
    pz=(min(f[1][0] for f in floors),max(f[1][1] for f in floors),min(f[1][2] for f in floors),max(f[1][3] for f in floors))
    pw=pz[1]-pz[0]; ph=pz[3]-pz[2]
    ax,ay=0.0,0.0
    def _hits(ox,oy):
        return any(pz[0]+ox<=W[1] and pz[1]+ox>=W[0] and pz[2]+oy-ph*1.6<=W[3] and pz[3]+oy>=W[2] for W in windows)
    if _hits(ax,ay) and windows:
        ax=max(W[1] for W in windows)+pw*0.5+300-pz[0]; ay=0.0
    bx,by=ax,ay-(ph*1.5+200*u)
    xb=E.blocks.new("x-plan"); xb.block.dxf.flags=4; xb.block.dxf.xref_path="x-plan.dwg"
    mb=E.blocks.new("x-marco"); mb.block.dxf.flags=4; mb.block.dxf.xref_path="x-marco.dwg"
    if "E-XREF" not in E.layers: E.layers.new("E-XREF").color=8
    msp.add_blockref("x-plan",(ax,ay),dxfattribs={"layer":"E-XREF"})
    msp.add_blockref("x-plan",(bx,by),dxfattribs={"layer":"E-XREF"})
    # ── scale: the template's scale if every floor fits its viewport, else next standard
    T=R["plan_layouts"][0] if R["plan_layouts"] else None
    vps=sorted(T["vps"],key=lambda v:v["cx"]) if T else []
    F=48.0
    if vps:
        F=vps[0]["vh"]/vps[0]["h"]
        need=max(max((b[1]-b[0])/(v["w"]*u),(b[3]-b[2])/(v["h"]*u)) for _,b in floors for v in vps)*1.08
        if need>F: F,_=pick_scale(need,ins_units)
    scale_label=next((lab for f_,lab in (MET_SCALES if ins_units in (4,5,6) else IMP_SCALES) if abs(f_-F)<1e-6),f"1:{int(round(F))}")
    made=[]; views=[]
    _default_layouts=[l.name for l in E.layouts if l.name!="Model"]
    def _clone_layout(L,newname,sheetno_txt,title_txt,vp_targets,floor_label=""):
        lay=E.layouts.new(newname); _copy_layout_setup(L["layout"],lay)
        imp2=Importer(ref,E)
        ents=list(L["ents"])+list(L["vtitles"])
        try: imp2.import_entities(ents,lay); imp2.finalize()
        except Exception: pass
        # the firm's own frame as an xref at the same spot
        mi=L["marco_ins"]
        lay.add_blockref("x-marco",(mi.dxf.insert.x,mi.dxf.insert.y) if mi else (0,0),dxfattribs={"layer":"E-XREF"})
        if L["sheetno"] is not None:
            s=L["sheetno"]; lay.add_text(sheetno_txt,dxfattribs={"layer":s.dxf.layer,"insert":(s.dxf.insert.x,s.dxf.insert.y),"height":s.dxf.height,
                                                               "style":s.dxf.style if s.dxf.style in E.styles else "STANDARD","rotation":s.dxf.rotation,"color":s.dxf.color})
        if L["title"] is not None:
            t=L["title"]; _ch=t.dxf.char_height*(0.78 if "\\P" in title_txt else 1.0)
            mt=lay.add_mtext(title_txt,dxfattribs={"layer":t.dxf.layer,"insert":(t.dxf.insert.x,t.dxf.insert.y),"char_height":_ch,
                                                            "style":t.dxf.style if t.dxf.style in E.styles else "STANDARD","attachment_point":t.dxf.attachment_point,"width":t.dxf.width})
        if floor_label:
            for e in lay:
                if e.dxftype()=="TEXT" and "%%U" in e.dxf.text: e.dxf.text=e.dxf.text.rstrip()+" - "+floor_label
        for v,(vcx,vcy,vh) in zip(sorted(L["vps"],key=lambda v:v["cx"]),vp_targets):
            vp=lay.add_viewport(center=(v["cx"],v["cy"]),size=(v["w"],v["h"]),view_center_point=(vcx,vcy),view_height=vh)
            vp.dxf.layer="DEFPOINTS" if "DEFPOINTS" in E.layers else "0"; vp.dxf.status=2
        made.append(newname); return lay
    # static sheets keep their own viewports verbatim
    stat_sorted=sorted(R["static_layouts"],key=lambda L:L["name"])
    plan_sorted=sorted(R["plan_layouts"],key=lambda L:L["name"])
    order=sorted(R["layouts"],key=lambda L:L["name"])
    for L in order:
        if L["kind"]=="static":
            title=L["title"].text if L["title"] is not None else ""
            _clone_layout(L,L["name"],L["name"],title,[(v["vx"],v["vy"],v["vh"]) for v in sorted(L["vps"],key=lambda v:v["cx"])])
        else:
            base=int(L["name"].split("-")[1])
            for i,(fname,b) in enumerate(floors):
                num=f"E-{base+i:03d}"
                label=(floor_names[i] if floor_names and i<len(floor_names) else fname) or f"LEVEL {i+1}"
                cx=(b[0]+b[1])/2; cy=(b[2]+b[3])/2
                tg=[]
                for j,v in enumerate(sorted(L["vps"],key=lambda v:v["cx"])):
                    ox,oy=(ax,ay) if j==0 else (bx,by)
                    tg.append((cx+ox,cy+oy,v["h"]*F*u))
                title=(L["title"].text if L["title"] is not None else "")+(f"\\P{label}" if len(floors)>1 else "")
                _clone_layout(L,num,num,title,tg,floor_label=label if len(floors)>1 else "")
                views.append(dict(sheet=num,floor=label,box=b))
    for ln in _default_layouts:
        try: E.layouts.delete(ln)
        except Exception: pass
    return E,made,scale_label,F,(ax,ay),(bx,by),views

def synthetic_fixtures(plan_doc,u,E,block="Light-1a"):
    """No reflected ceiling: one ceiling outlet per room at the room label,
       two along the long axis for big rooms. Returned as an RCP-style scratch
       doc so the normal lighting pass can switch and wire it."""
    RM=room_model(plan_doc,u); sc=ezdxf.new("R2000"); sm=sc.modelspace()
    sc.layers.new("E-LITE-EQPM")
    from ezdxf.addons import Importer
    if block in E.blocks:
        try: i_=Importer(E,sc); i_.import_block(block); i_.finalize()
        except Exception: pass
    if block not in sc.blocks: ensure_cca_block(sc,block) if block in CCA_SYMBOLS else sc.blocks.new(block).add_circle((0,0),0.15)
    SKIP=("STAIR","SHAFT","ROOF","PLANTER","POND","LOT","PROPERTY","N.I.C","DRYER","WASHER","OPEN SPACE")
    n=0
    walls=RM["walls"][:RM["nwall"]]
    for rid,(name,c) in enumerate(RM["rooms"]):
        if any(k in name.upper() for k in SKIP): continue
        # room extent from facing walls → long axis for a second fixture
        fac=[w for w in walls if RM["room_of"]((((w[0]+w[2])/2)+(-(w[3]-w[1]))/max(1e-9,math.hypot(w[2]-w[0],w[3]-w[1]))*8*u,((w[1]+w[3])/2)+((w[2]-w[0])/max(1e-9,math.hypot(w[2]-w[0],w[3]-w[1])))*8*u))==rid
             or RM["room_of"]((((w[0]+w[2])/2)-(-(w[3]-w[1]))/max(1e-9,math.hypot(w[2]-w[0],w[3]-w[1]))*8*u,((w[1]+w[3])/2)-((w[2]-w[0])/max(1e-9,math.hypot(w[2]-w[0],w[3]-w[1])))*8*u))==rid]
        pts=[]
        if fac:
            xs=[v for w in fac for v in (w[0],w[2])]; ys=[v for w in fac for v in (w[1],w[3])]
            W=max(xs)-min(xs); H=max(ys)-min(ys); area=W*H/(u*u)/144.0
            cx,cy=(min(xs)+max(xs))/2,(min(ys)+max(ys))/2
            if area>260 and max(W,H)>18*12*u:
                if W>=H: pts=[(cx-W/4,cy),(cx+W/4,cy)]
                else: pts=[(cx,cy-H/4),(cx,cy+H/4)]
            else: pts=[(cx,cy)]
            # keep inside the room; fall back to the label
            pts=[p for p in pts if RM["room_of"](p)==rid] or [c]
        else: pts=[c]
        for p in pts:
            sm.add_blockref(block,p,dxfattribs={"layer":"E-LITE-EQPM"}); n+=1
    return sc,n

def comm_pass(E, plan_doc, S, u, off):
    """Communications & smoke on copy B: smoke detector in every bedroom and in
       the hall/corridor serving them, one in the main living area per floor;
       TV + telephone outlets in bedrooms and living/family rooms."""
    msp=E.modelspace(); RM=room_model(plan_doc,u)
    walls=RM["walls"][:RM["nwall"]]; rooms=RM["rooms"]; room_of=RM["room_of"]
    ox,oy=off
    def blk(cands):
        for c in cands:
            if c in E.blocks: return c
        ensure_cca_block(E,cands[-1]) if cands[-1] in CCA_SYMBOLS else None
        return cands[-1] if cands[-1] in E.blocks else None
    SD=blk(["SD2","SD","Smoke","SMOKE"]); TV=blk(["CABLE","TV","Cable"]); TEL=blk(["Tel","TEL","Phone"])
    for ln in ("E-EQUIP","E-COMM"):
        if ln not in E.layers: E.layers.new(ln).color=4
    BED=("BEDROOM","DORM","RECAMARA","MASTER","HABITACION","CUARTO"); HALL=("HALL","CORRIDOR","FOYER","PASILLO","LANDING")
    MAIN=("LIVING","FAMILY","SALA","GREAT","KITCHEN","COCINA","DINING")
    nsd=ntv=0; placed=[]
    def put(name,p,rot=0):
        if not name: return
        msp.add_blockref(name,(p[0]+ox,p[1]+oy),dxfattribs={"layer":"E-EQUIP","color":256,"rotation":rot,"xscale":S,"yscale":S}); placed.append(p)
    # facing walls per room (longest first)
    facing={}
    for i,(x1,y1,x2,y2) in enumerate(walls):
        L=math.hypot(x2-x1,y2-y1)
        if L<36*u: continue
        dx,dy=(x2-x1)/L,(y2-y1)/L; n=(-dy,dx); mid=((x1+x2)/2,(y1+y2)/2)
        for side in (1,-1):
            rid=room_of((mid[0]+n[0]*side*8*u,mid[1]+n[1]*side*8*u))
            if rid is not None: facing.setdefault(rid,[]).append((L,i,side))
    mains_done=set()
    for rid,(name,c) in enumerate(rooms):
        U=name.upper()
        if any(k in U for k in BED+HALL):
            put(SD,c); nsd+=1
        elif any(k in U for k in MAIN):
            key=round(c[1]/(400*u))      # one per floor band
            if key not in mains_done: put(SD,c); nsd+=1; mains_done.add(key)
        if any(k in U for k in BED) or any(k in U for k in ("LIVING","FAMILY","SALA","GREAT")):
            lst=sorted(facing.get(rid,[]),reverse=True)
            if lst:
                L,i,side=lst[0]; x1,y1,x2,y2=walls[i]
                dx,dy=(x2-x1)/L,(y2-y1)/L; nin=(-dy*side,dx*side)
                rot=math.degrees(math.atan2(nin[1],nin[0]))
                put(TV,(x1+dx*L*0.42+nin[0]*1.5*u,y1+dy*L*0.42+nin[1]*1.5*u),rot)
                put(TEL,(x1+dx*L*0.58+nin[0]*1.5*u,y1+dy*L*0.58+nin[1]*1.5*u),rot); ntv+=1
    return dict(smoke=nsd,tv=ntv)

def marco_from_pdf(pdf_path, offset=(1.352439065425331,0.5313061044428613), logo_out=None):
    """Title-block xref rebuilt from a plotted set: keep only what is identical
       on every page (the frame), in layout paper inches."""
    import pdfplumber
    pdf=pdfplumber.open(str(pdf_path)); pages=pdf.pages; H=pages[0].height
    q=0.02
    r_=lambda v: round(v/q)
    def keyed(p):
        d={}
        for l in p.lines: d.setdefault(("L",r_(l["x0"]),r_(l["top"]),r_(l["x1"]),r_(l["bottom"])),("L",l))
        for rc in p.rects: d.setdefault(("R",r_(rc["x0"]),r_(rc["top"]),r_(rc["x1"]),r_(rc["bottom"])),("R",rc))
        for c in p.curves:
            pts=c.get("pts") or []
            d.setdefault(("C",)+tuple((r_(a),r_(b)) for a,b in pts[:6])+(len(pts),),("C",c))
        for w in p.extract_words(x_tolerance=1.5,y_tolerance=2,extra_attrs=["size"]):
            d.setdefault(("W",round(w["x0"]/0.03),round(w["top"]/0.03),w["text"]),("W",w))
        return d
    sets=[keyed(p) for p in pages]
    common={k:v for k,v in sets[0].items() if all(k in s for s in sets[1:])} if len(sets)>1 else sets[0]
    doc=ezdxf.new("R2000",setup=True); m=doc.modelspace()
    for ln in ("TB-LINE","TB-TEXT","TB-LOGO"): doc.layers.new(ln).color=7
    ox,oy=offset; X=lambda v: v/72.0-ox; Y=lambda v: (H-v)/72.0-oy
    for (t,v) in common.values():
        if t=="L": m.add_line((X(v["x0"]),Y(v["top"])),(X(v["x1"]),Y(v["bottom"])),dxfattribs={"layer":"TB-LINE"})
        elif t=="R":
            x0,x1,t0,b0=X(v["x0"]),X(v["x1"]),Y(v["top"]),Y(v["bottom"])
            if abs(x1-x0)<0.02 or abs(t0-b0)<0.02: m.add_line((x0,t0),(x1,b0),dxfattribs={"layer":"TB-LINE"})
            else:
                m.add_lwpolyline([(x0,b0),(x1,b0),(x1,t0),(x0,t0)],close=True,dxfattribs={"layer":"TB-LINE"})
                if v.get("fill") and (x1-x0)*(t0-b0)<2.0: m.add_solid((x0,b0),(x1,b0),(x0,t0),(x1,t0),dxfattribs={"layer":"TB-LOGO"})
        elif t=="C":
            pts=[(X(a),Y(b)) for a,b in (v.get("pts") or [])]
            if len(pts)>1:
                m.add_lwpolyline(pts,close=bool(v.get("fill")),dxfattribs={"layer":"TB-LOGO" if v.get("fill") else "TB-LINE"})
                if v.get("fill") and 3<=len(pts)<=400:
                    try: m.add_hatch(color=7,dxfattribs={"layer":"TB-LOGO"}).paths.add_polyline_path(pts,is_closed=True)
                    except Exception: pass
        elif t=="W":
            size=(v.get("size") or (v["bottom"]-v["top"]))/72.0
            _t=v["text"]
            _t=re.sub(r'^(\d)(\d)"=',r'\1/\2"=',_t)                          # 14"=1'-0"  → 1/4"=1'-0"
            _t=re.sub(r'^([A-Z]{3,9})(\d{4})$',r'\1/\2',_t)                   # JUNE2026  → JUNE/2026
            m.add_text(_t,dxfattribs={"layer":"TB-TEXT","height":max(0.03,size*0.72),"insert":(X(v["x0"]),Y(v["bottom"])+0.15*size)})
    # logo raster
    if logo_out and pages[0].images:
        try:
            im=pages[0].images[0]
            pages[0].crop((im["x0"],im["top"],im["x1"],im["bottom"])).to_image(resolution=300).save(str(logo_out))
            from PIL import Image
            img=Image.open(str(logo_out))
            idef=doc.add_image_def(filename=os.path.basename(str(logo_out)),size_in_pixel=img.size)
            m.add_image(idef,insert=(X(im["x0"]),Y(im["bottom"])),size_in_units=(X(im["x1"])-X(im["x0"]),Y(im["top"])-Y(im["bottom"])),dxfattribs={"layer":"TB-LOGO"})
        except Exception: pass
    return doc

def plot_set_pdf(E, plan_doc, marco_doc, out_pdf, png_dir=None):
    """Plot every layout to one PDF (and PNG per sheet) by binding the two
       xrefs into a throw-away copy and letting ezdxf render the viewports."""
    import io as _io, matplotlib
    matplotlib.use("Agg")
    from matplotlib.backends.backend_pdf import PdfPages
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import Configuration, ColorPolicy, LineweightPolicy
    from ezdxf.addons import Importer
    _ensure_fonts()
    buf=_io.StringIO(); E.write(buf); buf.seek(0); Fdoc=ezdxf.read(buf)
    for bname,src in (("x-plan",plan_doc),("x-marco",marco_doc)):
        if bname in Fdoc.blocks and src is not None:
            blk=Fdoc.blocks.get(bname); blk.block.dxf.flags=0
            try:
                i_=Importer(src,Fdoc); i_.import_entities(list(src.modelspace()),blk); i_.finalize()
            except Exception as ex: print("bind",bname,ex)
    pngs=[]
    cfg=Configuration(color_policy=ColorPolicy.BLACK,lineweight_policy=LineweightPolicy.ABSOLUTE,min_lineweight=0.08)
    with PdfPages(str(out_pdf)) as pdf:
        for lay in sorted([l for l in Fdoc.layouts if l.name!="Model"],key=lambda l:l.name):
            pw=lay.dxf.paper_width/25.4 if lay.dxf.paper_width>100 else lay.dxf.paper_width
            ph=lay.dxf.paper_height/25.4 if lay.dxf.paper_height>100 else lay.dxf.paper_height
            if lay.dxf.plot_rotation in (1,3): pw,ph=ph,pw
            fig=plt.figure(figsize=(pw,ph)); ax=fig.add_axes([0,0,1,1]); ax.set_axis_off()
            try:
                Frontend(RenderContext(Fdoc),MatplotlibBackend(ax),config=cfg).draw_layout(lay,finalize=False)
            except Exception as ex: print("plot",lay.name,ex)
            try:
                for im in (marco_doc.modelspace() if marco_doc is not None else []):
                    if im.dxftype()!="IMAGE": continue
                    fn=im.image_def.dxf.filename
                    cand=[Path(fn)]+[Path(d)/Path(fn).name for d in (getattr(marco_doc,"_logo_dir",None),Path(out_pdf).parent) if d]
                    fp=next((q for q in cand if q.exists()),None)
                    if fp is None: continue
                    import matplotlib.image as mpimg
                    arr=mpimg.imread(str(fp)); x0,y0=im.dxf.insert.x,im.dxf.insert.y
                    w_=im.dxf.u_pixel.x*im.dxf.image_size.x; h_=im.dxf.v_pixel.y*im.dxf.image_size.y
                    ax.imshow(arr,extent=(x0,x0+w_,y0,y0+h_),zorder=50,aspect="auto")
            except Exception as ex: print("logo plot",ex)
            lm=lay.dxf.limmin; LM=lay.dxf.limmax
            ax.set_xlim(lm.x,lm.x+pw); ax.set_ylim(lm.y,lm.y+ph); ax.set_aspect("equal")
            pdf.savefig(fig,facecolor="white")
            if png_dir:
                p=Path(png_dir)/f"sheet_{lay.name}.png"; fig.savefig(str(p),dpi=60,facecolor="white"); pngs.append((lay.name,p))
            plt.close(fig)
    return pngs

def _png_svg(png_path):
    import base64
    from PIL import Image
    im=Image.open(str(png_path)); w,h=im.size
    b=base64.b64encode(open(str(png_path),"rb").read()).decode()
    return f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {w} {h}" width="{w}" height="{h}"><image href="data:image/png;base64,{b}" width="{w}" height="{h}"/></svg>'

def process_reference_set(ref_path, plan_paths, pdf_path, marco_path, tmp, debug):
    import zipfile, io, shutil
    ref=read_dxf_any(ref_path); R=load_reference_set(ref)
    if not plan_paths: return None,None,"Reference set found but no architectural plan to work on."
    dxf_stems={p.stem.lower() for p in plan_paths}
    part=extract_plan(plan_paths[0],plan_paths,dxf_stems); debug+=part["debug"]
    base=part["doc"]; ins_units=part["ins_units"] or 1
    u=UNIT_PER_IN.get(ins_units,1.0)
    # floors from zones (ground-floor keywords first)
    fw=[((e.dxf.start.x+e.dxf.end.x)/2,(e.dxf.start.y+e.dxf.end.y)/2) for e in base.modelspace() if e.dxftype()=="LINE" and e.dxf.layer=="A-WALL"]
    zs=list(part["zones"])
    _GK=("KITCHEN","COCINA","LIVING","SALA","GARAGE","CARPORT","ENTRANCE","FOYER","DINING","COMEDOR","MARQUESINA")
    def _g(z): return -sum(1 for e in base.modelspace() if e.dxftype()=="TEXT" and e.dxf.layer=="A-AREA-IDEN" and z[0]<=e.dxf.insert.x<=z[1] and z[2]<=e.dxf.insert.y<=z[3] and any(k in e.dxf.text.upper() for k in _GK))
    zs.sort(key=lambda z:(_g(z),z[0]))
    LEVELS=["FIRST FLOOR","SECOND FLOOR","THIRD FLOOR","FOURTH FLOOR"]
    floors=[]
    for i,z in enumerate(zs):
        pts=[p for p in fw if z[0]<=p[0]<=z[1] and z[2]<=p[1]<=z[3]]
        b=(min(p[0] for p in pts),max(p[0] for p in pts),min(p[1] for p in pts),max(p[1] for p in pts)) if len(pts)>=20 else z
        floors.append((LEVELS[i] if i<4 else f"LEVEL {i+1}",b))
    E,made,scale_label,F,offA,offB,views=build_from_reference(base,floors,R,ins_units)
    S=F*u
    # lighting without a ceiling plan
    lighting=power=comm=None
    try:
        sc,nfx=synthetic_fixtures(base,u,E)
        from ezdxf.addons import Importer
        fx=[e for e in sc.modelspace() if e.dxftype()=="INSERT"]
        if "E-LITE-EQPM" not in E.layers: E.layers.new("E-LITE-EQPM").color=7
        i_=Importer(sc,E); i_.import_entities(fx,E.modelspace()); i_.finalize()
        for e in list(E.modelspace())[-len(fx):]:
            try: e.dxf.xscale=S; e.dxf.yscale=S; e.translate(offA[0],offA[1],0)
            except Exception: pass
        lighting=lighting_pass(E,base,sc,S,u)
    except Exception as ex: debug.append(f"lighting error: {ex}")
    try: power=power_pass(E,base,S,u,offA[0]); power["ox"]=offA[0]
    except Exception as ex: debug.append(f"power error: {ex}")
    try: comm=comm_pass(E,base,S,u,offB)
    except Exception as ex: debug.append(f"comm error: {ex}")
    # frame
    outdir=tmp/"out"; outdir.mkdir(exist_ok=True)
    marco=None; msrc=""
    if marco_path:
        try: marco=read_dxf_any(marco_path); msrc=marco_path.name
        except Exception as ex: debug.append(f"marco read: {ex}")
    if marco is None and pdf_path:
        try: marco=marco_from_pdf(pdf_path,logo_out=outdir/"x-marco_logo.png"); msrc=f"rebuilt from {pdf_path.name}"
        except Exception as ex: debug.append(f"marco from pdf: {ex}")
    if marco is None: marco=ezdxf.new("R2000"); msrc="missing (xref only)"
    files={"x-plan.dxf":base,"x-marco.dxf":marco,"E-Electrical Plans.dxf":E}
    dwg_ok=[]; dwg_fail=[]
    for name,doc in files.items():
        p=outdir/name; doc.saveas(str(p))
        d=dxf_to_dwg(p); (dwg_ok if d else dwg_fail).append(name)
    previews=[]
    try:
        pngs=plot_set_pdf(E,base,marco,outdir/"E-Electrical Plans.pdf",png_dir=tmp)
        for name,p in pngs: previews.append((f"Sheet {name}",_png_svg(p)))
    except Exception as ex: debug.append(f"pdf: {ex}")
    try:
        for k,v in build_previews(outdir,[(n,b) for n,b in floors],None,False,power_ox=offA[0]): 
            if v: previews.append((k,v))
    except Exception as ex: debug.append(f"previews: {ex}")
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
        for p in sorted(outdir.iterdir()): z.write(p,p.name)
    shutil.rmtree(tmp,ignore_errors=True)
    msg=(f"Done (reference set {ref_path.name}). {len(floors)} floor(s): {', '.join(n for n,_ in floors)} → x-plan, x-marco, E-Electrical Plans (DWG+PDF); "
         f"sheets {', '.join(made)} at {scale_label}; frame {msrc}")
    if lighting: msg+=f"; LIGHTING {lighting['fixtures']} fixtures, {lighting['switches']} switches, {lighting['runs']} runs"
    if power: msg+=f"; POWER {power['receptacles']} receptacles"
    if comm: msg+=f"; COMM {comm['smoke']} smoke detectors, {comm['tv']} TV/tel pairs"
    if dwg_fail: msg+=f"; DWG conversion failed for {', '.join(dwg_fail)}"
    if debug: msg+=" | "+" | ".join(debug)
    return (buf.getvalue(),previews), msg+".", None


# ── UI ────────────────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages=[{"role":"assistant","content":"Upload the architect's DWG/DXF files (floor plan sheets, reflected ceiling), then click Process."}]
if "result" not in st.session_state: st.session_state.result=None
if "processed_files" not in st.session_state: st.session_state.processed_files=set()

st.markdown("<h4 style='text-align:center; padding: 20px 0 10px;'>NEC Placer</h4>",unsafe_allow_html=True)
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.write(msg["content"])
if st.session_state.result:
    zip_bytes,previews=st.session_state.result
    with st.chat_message("assistant"):
        shown=[(k,v) for k,v in previews if v]
        if shown:
            import streamlit.components.v1 as components
            tabs=st.tabs([k for k,_ in shown])
            for tab,(k,v) in zip(tabs,shown):
                with tab: components.html(viewer_html(v),height=760)
        st.download_button("Download project (x-plan, x-marco, E-Electrical Plan)",
            data=zip_bytes,file_name="nec-project.zip",
            mime="application/zip",type="primary",use_container_width=True)
uploaded=st.file_uploader("Upload files",type=["dxf","dwg","pdf"],accept_multiple_files=True,label_visibility="collapsed")
if uploaded:
    col1,col2=st.columns([3,1])
    with col2: process_btn=st.button("Process",use_container_width=True,type="primary")
    if process_btn:
        file_key=frozenset(f.name for f in uploaded)
        if file_key not in st.session_state.processed_files:
            st.session_state.processed_files.add(file_key)
            st.session_state.messages.append({"role":"user","content":"Uploaded: "+", ".join(f.name for f in uploaded)})
            with st.chat_message("assistant"):
                with st.spinner("Processing..."):
                    result,msg,error=process_files(uploaded)
                    if error: st.session_state.messages.append({"role":"assistant","content":error})
                    else:
                        st.session_state.result=result
                        st.session_state.messages.append({"role":"assistant","content":msg})
            st.rerun()
