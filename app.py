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
        orig = os.getcwd()
        os.chdir(dwg_path.parent)
        subprocess.run(["dwg2dxf", dwg_path.name], capture_output=True, timeout=60)
        os.chdir(orig)
        out = dwg_path.with_suffix(".dxf")
        if out.exists(): return out
    except: pass
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

def iter_leaves(ents, doc, max_depth, ceil_fn=None, skip=()):
    """Yield (leaf_entity, in_ceil) walking INSERTs with ezdxf's own resolver,
    so base points, mirrored inserts, non-uniform scale and OCS are all right."""
    def rec(es, d, in_ceil):
        for e in es:
            try:
                lay=getattr(e.dxf,'layer','0')
                if lay in skip: continue
                if e.dxftype()=="INSERT":
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

    zones=[]; zone_src=[]
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
    def _cluster_walls(pts):
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
        for comp in comps[:6]:
            if len(comp)<0.25*top: break
            b=_bb(comp); px=0.10*(b[1]-b[0]); py=0.10*(b[3]-b[2])
            out.append((b[0]-px,b[1]+px,b[2]-py,b[3]+py))
        return out

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
        zones=_cluster_walls(wall_pts)
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
    for ln,col in (("A-CLNG",9),("E-LITE-EQPM",33)):
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
    n=0
    for ve,in_ceil in iter_leaves(src_msp,src_doc,5,ceil_fn=is_ceiling_layer,skip=set(SKIP)):
        if n>60000: break
        try:
            lay=getattr(ve.dxf,'layer','0')
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
          f"{'mirrored' if use_mirror else 'aligned'}, added={n}]")
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
    if border is None: return marco, info
    info["found"]=True
    vx1=bvp.dxf.center.x-bvp.dxf.width/2; vx2=bvp.dxf.center.x+bvp.dxf.width/2
    vy1=bvp.dxf.center.y-bvp.dxf.height/2; vy2=bvp.dxf.center.y+bvp.dxf.height/2
    right=max(bext[1],vx2); top=max(bext[3],vy2); info["right"]=right; info["top"]=top
    logo=None
    try:
        for x in a.blocks[border.dxf.name]:
            if x.dxftype()=="IMAGE":
                p=x.dxf.insert; logo=(p.x+border.dxf.insert.x,p.y+border.dxf.insert.y); break
    except: pass
    # bring everything across WITH styles, then explode the border
    imp=Importer(a,marco)
    imp.import_block(border.dxf.name)
    ins=mm.add_blockref(border.dxf.name,(border.dxf.insert.x,border.dxf.insert.y),
                        dxfattribs={"xscale":border.dxf.xscale,"yscale":border.dxf.yscale,"rotation":border.dxf.rotation})
    for at in (border.attribs or []):
        try: ins.add_attrib(at.dxf.tag or "*",at.dxf.text,at.dxf.insert,dxfattribs={"height":at.dxf.height,"rotation":at.dxf.rotation,"style":at.dxf.style,"layer":at.dxf.layer})
        except Exception: pass
    loose=[e for e in best if e.dxftype() in ("MTEXT","TEXT")]
    imp.import_entities(loose,mm); imp.finalize()
    loose_h={e.dxf.handle for e in mm if e.dxftype() in ("MTEXT","TEXT")}
    ins.explode()
    for e in list(mm):
        if e.dxftype() in ("IMAGE","WIPEOUT","ATTDEF"): mm.delete_entity(e)
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
          "E-WIREPOLES":3,"0-Vport":4,"G-ANNO-TTLB":6,"G-ANNO-NPLT":7,"A-CLNG":9,"E-SYMB":7}
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
        for e in rcp_scratch.modelspace():
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
    return E, made, scale_label

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

def build_previews(outdir, floors, rcp_scratch, has_rcp):
    """Floor plans (labels), lighting copy with the RCP, and the E-101 sheet with its frame."""
    previews=[]
    try:
        plan=ezdxf.readfile(str(outdir/"x-plan.dxf"))
        views=[(f"Plan — {n}" if n else "Plan",b) for n,b in floors] if floors else [("Plan",None)]
        for k,v in render_svgs(plan,views).items(): previews.append((k,v))
        if has_rcp and rcp_scratch is not None:
            for e in rcp_scratch.modelspace():
                g=leaf_geom(e,0.5)
                if g: write_leaf(plan.modelspace(),g,{"layer":e.dxf.layer,"color":256})
            if "A-CLNG" not in plan.layers: plan.layers.new("A-CLNG").color=9
            if "E-LITE-EQPM" not in plan.layers: plan.layers.new("E-LITE-EQPM").color=33
            for k,v in render_svgs(plan,[(f"Lighting + RCP — {n}" if n else "Lighting + RCP",b) for n,b in floors],black=False).items(): previews.append((k,v))
    except Exception as ex:
        previews.append(("Plan (render failed)",None)); print("preview:",ex)
    try:
        from ezdxf.addons import Importer
        marco=ezdxf.readfile(str(outdir/"x-marco.dxf")); E=ezdxf.readfile(str(outdir/"E-Electrical Plan.dxf"))
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
    floors=[]
    for i,(z,nms) in enumerate(floor_zones):
        pts=[p for p in fw if z[0]<=p[0]<=z[1] and z[2]<=p[1]<=z[3]]
        b=(min(p[0] for p in pts),max(p[0] for p in pts),min(p[1] for p in pts),max(p[1] for p in pts)) if len(pts)>=20 else z
        name="" if len(floor_zones)==1 else (f"PLAN {'/'.join(dict.fromkeys(nms))}" if nms else f"LEVEL {i+1}")
        floors.append((name,b))
    E,made,scale_label=build_electrical(union,rcp_scratch,minfo,ins_units,project,split,
                                        fbox=fbox if len(fw)>=20 else None, floors=floors)
    files["E-Electrical Plan.dxf"]=E

    # write, convert, zip
    dwg_ok=[]; dwg_fail=[]
    for name,doc in files.items():
        p=outdir/name; doc.saveas(str(p))
        d=dxf_to_dwg(p)
        (dwg_ok if d else dwg_fail).append(name)
    try: previews=build_previews(outdir,floors,rcp_scratch,rcp_scratch is not None)
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
    if dwg_fail: msg+=f"; DWG conversion failed for {', '.join(dwg_fail)} (DXF included)"
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
uploaded=st.file_uploader("Upload files",type=["dxf","dwg"],accept_multiple_files=True,label_visibility="collapsed")
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
