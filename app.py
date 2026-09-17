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
</style>
""", unsafe_allow_html=True)

if not st.session_state.get("authenticated"):
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1,1,1])
    with col2:
        st.markdown("### NEC Placer")
        pw = st.text_input("Password", type="password", label_visibility="collapsed", placeholder="Password")
        if st.button("Continue", use_container_width=True):
            if pw == os.environ.get("password", "necplacer2025"):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password")
    st.stop()

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
    "HDR","HEADER","SILL","LINTEL"}
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
    return any(k in n for k in ["rcp","reflected","ceiling plan","a103"])

CEIL_LAYER_KW=("CLNG","CEIL","LITE","LIGHT","LAMP","LUM","PLAF","CIELO",
               "LUZ","LUCES","ILUMIN")
CEIL_BLOCK_KW=("LIGHT","LITE","LAMP","LUM","FIXT","DOWNL","RECESS","TROFFER",
               "LUZ","ILUMIN")
RCP_TEXT_KW=("REFLECTED","CEILING PLAN","RCP","PLAFON","PLAFÓN","CIELO RASO")

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

def build_project(out, zones, ins_units, project, has_rcp):
    """Turn the cleaned model into a job: floor blocks, discipline copies,
    paper-space sheets with title blocks + viewports, legend sheet."""
    import datetime
    msp=out.modelspace()
    date_str=datetime.date.today().strftime("%m/%d/%Y")
    u_per_in=UNIT_PER_IN.get(ins_units,1.0)
    units_known=ins_units in UNIT_PER_IN

    # 1. One block per floor; route every model entity into its floor block
    order=sorted(range(len(zones)), key=lambda i:-(zones[i][2]+zones[i][3])/2)
    names={zi:f"PLAN-{chr(65+k)}" for k,zi in enumerate(order)}
    fblk={zi:out.blocks.new(f"ARCH-{names[zi]}") for zi in order}
    rblk={zi:out.blocks.new(f"RCP-{names[zi]}")  for zi in order}
    for zi in order:
        X1,X2,Y1,Y2=zones[zi]
        fblk[zi].block.dxf.base_point=(X1,Y1,0)
        rblk[zi].block.dxf.base_point=(X1,Y1,0)

    def ref_pt(e):
        t=e.dxftype()
        try:
            if t=="LINE":   return ((e.dxf.start.x+e.dxf.end.x)/2,(e.dxf.start.y+e.dxf.end.y)/2)
            if t in ("ARC","CIRCLE"): return (e.dxf.center.x,e.dxf.center.y)
            if t in ("TEXT","MTEXT","INSERT"): return (e.dxf.insert.x,e.dxf.insert.y)
            if t=="LWPOLYLINE":
                pts=list(e.get_points())
                return (sum(p[0] for p in pts)/len(pts),sum(p[1] for p in pts)/len(pts))
        except: pass
        return None
    def zone_of(x,y):
        for zi in order:
            X1,X2,Y1,Y2=zones[zi]
            if X1<=x<=X2 and Y1<=y<=Y2: return zi
        return min(order,key=lambda zi:abs((zones[zi][2]+zones[zi][3])/2-y))
    for e in list(msp):
        p=ref_pt(e)
        if p is None: continue
        zi=zone_of(*p)
        tgt = rblk[zi] if e.dxf.layer=="A-RCP" else fblk[zi]
        msp.move_to_layout(e,tgt)

    # 2. Discipline copies side by side in model space
    DISC=[("POWER","1"),("LIGHTING","2"),("TELECOM","3")]
    W=max(z[1]-z[0] for z in zones); H=max(z[3]-z[2] for z in zones)
    gapx=W*0.15; gapy=H*0.30; th=max(H*0.025,1.0)
    copies={}   # (zi,disc) -> (cx,cy)
    for r,zi in enumerate(order):
        X1,X2,Y1,Y2=zones[zi]; w=X2-X1; h=Y2-Y1
        for col,(dname,_) in enumerate(DISC):
            ox=col*(W+gapx); oy=-r*(H+gapy)
            msp.add_blockref(fblk[zi].name,(ox,oy))
            if dname=="LIGHTING" and has_rcp:
                msp.add_blockref(rblk[zi].name,(ox,oy))
            msp.add_text(f"{dname} PLAN - {names[zi]}",dxfattribs={
                "layer":"E-ANNO-TITL","color":256,"insert":(ox,oy+h+th),"height":th})
            copies[(zi,dname)]=(ox+w/2,oy+h/2,w,h)

    # 3. Paper space: ARCH D landscape, border + title strip + viewport
    PW,PH,M,TB=36.0,24.0,0.5,3.5
    def sheet(num,title,scale_label="",vp=None):
        lay=out.layouts.new(num)
        lay.page_setup(size=(PW,PH),margins=(0,0,0,0),units="inch")
        L="E-ANNO-TTLB"; T="E-ANNO-TEXT"
        lay.add_lwpolyline([(M,M),(PW-M,M),(PW-M,PH-M),(M,PH-M)],close=True,dxfattribs={"layer":L,"color":256})
        sx=PW-M-TB
        lay.add_line((sx,M),(sx,PH-M),dxfattribs={"layer":L,"color":256})
        rows=[PH-M-3.0, PH-M-6.5, PH-M-9.0, PH-M-11.5, PH-M-14.0, PH-M-16.5, M+3.0]
        for y in rows: lay.add_line((sx,y),(PW-M,y),dxfattribs={"layer":L,"color":256})
        def tx(s,x,y,h=0.12,lay_=T):
            lay.add_text(s,dxfattribs={"layer":lay_,"color":256,"insert":(x,y),"height":h})
        x0=sx+0.15
        tx("FIRM / LOGO",x0,PH-M-1.6,0.18)
        tx("PROJECT",x0,PH-M-3.4,0.10);        tx(project[:34].upper(),x0,PH-M-4.6,0.16)
        tx("SHEET TITLE",x0,PH-M-6.9,0.10);    tx(title,x0,PH-M-8.1,0.16)
        tx("DATE",x0,PH-M-9.4,0.10);           tx(date_str,x0,PH-M-10.4,0.14)
        tx("SCALE",x0,PH-M-11.9,0.10);         tx(scale_label or "AS NOTED",x0,PH-M-12.9,0.14)
        tx("DRAWN BY",x0,PH-M-14.4,0.10);      tx("________",x0,PH-M-15.4,0.14)
        tx("CHECKED BY",x0,PH-M-16.9,0.10);    tx("________",x0,PH-M-17.9,0.14)
        tx("SHEET NO.",x0,M+2.4,0.10);         tx(num.split(" ")[0],x0,M+0.9,0.55,"E-ANNO-TITL")
        if vp:
            cx,cy,w,h=vp
            vx1,vy1,vx2,vy2=M+0.5,M+0.5,sx-0.5,PH-M-1.4
            vw,vh=vx2-vx1,vy2-vy1
            need=max(w/(vw*u_per_in),h/(vh*u_per_in))
            F,label=pick_scale(need,ins_units)
            if not units_known: label=f"{label} (VERIFY UNITS)"
            lay.add_viewport(center=((vx1+vx2)/2,(vy1+vy2)/2),size=(vw,vh),
                             view_center_point=(cx,cy),view_height=vh*F*u_per_in,
                             dxfattribs={"layer":"DEFPOINTS"})
            tx(f"{title}   SCALE: {label}",vx1,vy2+0.3,0.22,"E-ANNO-TITL")
            for ent in lay:
                if ent.dxftype()=="TEXT" and ent.dxf.text=="AS NOTED": ent.dxf.text=label
        return num

    made=[]
    # E-001 legend + general notes
    made.append(sheet("E-001 LEGEND","LEGEND & GENERAL NOTES"))
    lay=out.layouts.get("E-001 LEGEND")
    _make_symbols(out)
    x=M+1.0; y=PH-M-1.2
    lay.add_text("ELECTRICAL LEGEND",dxfattribs={"layer":"E-ANNO-TITL","color":256,"insert":(x,y),"height":0.3})
    y-=0.9
    for bname,desc in [("E-RECP-DUPLEX","DUPLEX RECEPTACLE, 20A-125V, NEMA 5-20R"),
                       ("E-RECP-GFCI","DUPLEX RECEPTACLE, GFCI"),
                       ("E-SWCH-1P","SINGLE POLE SWITCH"),
                       ("E-LITE-CLNG","CEILING MOUNTED LIGHT FIXTURE"),
                       ("E-LITE-2X4","2'x4' RECESSED LED TROFFER"),
                       ("E-DATA","DATA / TELECOM OUTLET"),
                       ("E-JBOX","JUNCTION BOX"),
                       ("E-PANL","PANELBOARD, SURFACE MOUNTED")]:
        lay.add_blockref(bname,(x+0.3,y),dxfattribs={"layer":"E-SYMB","color":256})
        lay.add_text(desc,dxfattribs={"layer":"E-ANNO-TEXT","color":256,"insert":(x+1.0,y-0.07),"height":0.14})
        y-=0.65
    gx=M+14.0; gy=PH-M-1.2
    lay.add_text("GENERAL NOTES",dxfattribs={"layer":"E-ANNO-TITL","color":256,"insert":(gx,gy),"height":0.3})
    gy-=0.9
    for i,n in enumerate([
        "ALL WORK SHALL COMPLY WITH THE NATIONAL ELECTRICAL CODE (NFPA 70), LATEST ADOPTED EDITION, AND ALL LOCAL AMENDMENTS.",
        "CONTRACTOR SHALL VERIFY ALL EXISTING CONDITIONS AND DIMENSIONS IN THE FIELD PRIOR TO ROUGH-IN.",
        "ALL RECEPTACLES IN KITCHENS, BATHROOMS, GARAGES, OUTDOORS AND WITHIN 6 FT OF SINKS SHALL BE GFCI PROTECTED PER NEC 210.8.",
        "ALL BRANCH CIRCUITS SERVING DWELLING UNIT AREAS PER NEC 210.12 SHALL BE AFCI PROTECTED.",
        "PROVIDE EQUIPMENT GROUNDING CONDUCTOR IN ALL RACEWAYS. SIZE PER NEC 250.122.",
        "MOUNTING HEIGHTS (TO CENTERLINE, U.N.O.): RECEPTACLES 18\", SWITCHES 48\", COUNTER RECEPTACLES 42\".",
        "COORDINATE ALL LIGHT FIXTURE LOCATIONS WITH THE REFLECTED CEILING PLAN AND MECHANICAL EQUIPMENT.",
        "ARCHITECTURAL BACKGROUND SHOWN FOR REFERENCE ONLY. REFER TO ARCHITECTURAL DRAWINGS FOR DIMENSIONS.",
    ],1):
        lay.add_text(f"{i}.  {n}",dxfattribs={"layer":"E-ANNO-TEXT","color":256,"insert":(gx,gy),"height":0.13})
        gy-=0.45

    # Discipline sheets, one per floor
    for dname,series in DISC:
        for k,zi in enumerate(order):
            num=f"E-{series}{k+1:02d} {dname} {names[zi]}"
            made.append(sheet(num,f"{dname} PLAN - {names[zi]}",vp=copies[(zi,dname)]))

    for junk in ("Layout1","Layout2"):
        try:
            if junk in out.layouts: out.layouts.delete(junk)
        except: pass
    return made

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

def process_files(uploaded_files):
    tmp = Path(tempfile.mkdtemp())
    dxf_paths = []
    conversion_errors = []

    for f in uploaded_files:
        p = tmp / f.name
        p.write_bytes(f.read())
        if f.name.lower().endswith(".dwg"):
            st.write(f"Converting {f.name}...")
            converted = dwg_to_dxf(p)
            if converted:
                dxf_paths.append(converted)
                st.write(f"✓ Done")
            else:
                conversion_errors.append(f.name)
        else:
            dxf_paths.append(p)

    if conversion_errors and not dxf_paths:
        return None, None, f"Could not convert: {', '.join(conversion_errors)}"
    if not dxf_paths:
        return None, None, "No readable files found."

    dxf_stems = {p.stem.lower() for p in dxf_paths}
    rcp_paths = [p for p in dxf_paths if is_rcp(p.name)]
    floor_paths = [p for p in dxf_paths if not is_rcp(p.name)]
    if not floor_paths: floor_paths = dxf_paths

    file_meta = []
    for p in floor_paths:
        file_meta.append((p,) + quick_scan(p))
    gc.collect()

    if not file_meta:
        return None, None, "Could not read any files."

    candidates = [m for m in file_meta if m[1]]
    if not candidates:
        candidates = list(file_meta)
    sheet_path = max(candidates, key=lambda m: sheet_score(m[0].name))[0]

    doc_a1 = ezdxf.readfile(str(sheet_path))

    xref_name=None; xref_ix=xref_iy=0.0; xref_sx=xref_sy=1.0; xref_rot=0.0
    for e in doc_a1.modelspace():
        try:
            if e.dxftype()=="INSERT" and e.dxf.name not in SKIP_BLOCKS:
                if e.dxf.name.lower() in dxf_stems:
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
        master_path=next((p for p in dxf_paths if p.stem.lower()==xref_name.lower()),None)
        doc_m=ezdxf.readfile(str(master_path)) if master_path else doc_a1
    else:
        master_path=None
        doc_m=doc_a1

    # Content-based RCP detection: a non-sheet, non-master file whose ceiling
    # evidence clearly beats the sheet's, or whose title calls it a ceiling plan.
    sheet_meta=next((m for m in file_meta if m[0]==sheet_path),None)
    sheet_ratio=(sheet_meta[4]/sheet_meta[5]) if (sheet_meta and sheet_meta[5]) else 0.0
    for (p,vp,w,x,ceil,total,rtxt) in file_meta:
        if p==sheet_path or p==master_path or p in rcp_paths: continue
        if sheet_score(p.name)==0: continue   # site/roof sheets are never RCP
        ratio=(ceil/total) if total else 0.0
        by_layers = ceil>=20 and ratio>=0.10 and ratio>=2*sheet_ratio
        by_title  = rtxt and ceil>=10 and ratio>=0.03
        if by_layers or by_title:
            rcp_paths.append(p)

    msp_m=doc_m.modelspace()

    zones=[]
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
            except: pass

    if xref_name and zones:
        lot_x1=min(z[0] for z in zones); lot_x2=max(z[1] for z in zones)
        covered_y1=min(z[2] for z in zones); covered_y2=max(z[3] for z in zones)
        wall_ys=[]
        for e in msp_m:
            try:
                if e.dxftype()=="LINE" and getattr(e.dxf,'layer','') in \
                   ["AP-WALL","AR-WALLS","A-WALL","WALL"]:
                    cx=(e.dxf.start.x+e.dxf.end.x)/2
                    cy=(e.dxf.start.y+e.dxf.end.y)/2
                    if lot_x1<=cx<=lot_x2 and cy<0 and \
                       not (covered_y1<=cy<=covered_y2):
                        wall_ys.append(cy)
            except: pass
        if wall_ys:
            bands={}
            for y in wall_ys:
                b=round(y/500)*500; bands[b]=bands.get(b,0)+1
            sb=sorted(bands.keys())
            clusters,cur=[],([sb[0]] if sb else [])
            for i in range(1,len(sb)):
                if sb[i]-sb[i-1]<=1000: cur.append(sb[i])
                else: clusters.append(cur); cur=[sb[i]]
            if cur: clusters.append(cur)
            valid=[c for c in clusters
                   if sum(bands.get(b,0) for b in c)>=20 and sum(c)/len(c)<0]
            if valid:
                best=max(valid,key=lambda c:sum(c)/len(c))
                y1=min(best)-1500; y2=max(best)+1500
                cy=(y1+y2)/2
                if not any(Z1<=cy<=Z2 for _,_,Z1,Z2 in zones):
                    vp_x1=min(z[0] for z in zones)
                    vp_x2=max(z[1] for z in zones)
                    zones.append((vp_x1,vp_x2,y1,y2))

    if not zones:
        zones=[(-1e9,1e9,-1e9,1e9)]

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
            if is_grid_layer(layer):
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
                    if is_grid_layer(src_lay) and len(txt)<=4:
                        lay_out,zs="S-GRID-IDEN",ezones
                    elif is_grid_id(txt):
                        # grid ids off a grid layer must sit in the ring outside the plan
                        if inzone(ix,iy,zones): continue
                        lay_out,zs="S-GRID-IDEN",ezones
                    elif is_room_label(txt):
                        lay_out,zs="A-ANNO-TEXT",zones
                    else:
                        continue
                    if inzone(ix,iy,zs):
                        out_msp.add_text(txt[:50],dxfattribs={
                            "layer":lay_out,"color":256,
                            "insert":(ix,iy),"height":h,"rotation":txt_rot})
                        placed_labels+=1
            except: pass
    else:
        # Xref: try A-1 transform first (works with ODA), then Master text fallback
        tl=[]
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
                    if is_grid_id(txt) or (is_grid_layer(src_lay) and len(txt)<=4): lay_out="S-GRID-IDEN"
                    elif is_room_label(txt): lay_out="A-ANNO-TEXT"
                    else: continue
                    mx,my=a1_to_master(ix,iy)
                    txt_rot=txt_rot-math.degrees(xref_rot)
                    if my<1000:
                        tl.append((mx,my,txt,h*xref_sx,txt_rot,lay_out))
            except: pass
        # Fallback: read directly from Master, find nearest X cluster to zone
        if not tl:
            raw=[]
            for e in msp_m:
                try:
                    if e.dxftype() in ["TEXT","MTEXT"]:
                        if e.dxftype()=="TEXT":
                            txt=e.dxf.text.strip(); ix,iy=e.dxf.insert.x,e.dxf.insert.y
                            h=e.dxf.height; txt_rot=getattr(e.dxf,'rotation',0.0)
                        else:
                            txt=clean_mtext(e.text); ix,iy=e.dxf.insert.x,e.dxf.insert.y
                            h=getattr(e.dxf,'char_height',20); txt_rot=getattr(e.dxf,'rotation',0.0)
                        if len(txt)<2: continue
                        raw.append((ix,iy,txt,h,txt_rot))
                except: pass
            if raw:
                xs=sorted(set(round(ix/500)*500 for ix,iy,t,h,r in raw))
                best_x=min(xs,key=lambda x:abs(x-zone_x_center))
                cluster=[item for item in raw if abs(item[0]-best_x)<=2000]
                if cluster:
                    cx=sum(ix for ix,iy,t,h,r in cluster)/len(cluster)
                    xc=zone_x_center-cx
                    for ix,iy,txt,h,txt_rot in cluster:
                        tl.append((ix+xc,iy,txt,h,txt_rot))
        # Per-zone X correction: compute avg_x from labels in each zone Y range
        # then shift that avg to zone center — works for both ground floor and basement
        for (X1,X2,Y1,Y2) in zones:
            zone_cx=(X1+X2)/2
            zone_buf=300
            zone_lbls=[it for it in tl if (Y1-zone_buf)<=it[1]<=(Y2+zone_buf)]
            if not zone_lbls: continue
            avg_mx=sum(it[0] for it in zone_lbls)/len(zone_lbls)
            xc=zone_cx-avg_mx
            for mx,my,txt,h,txt_rot,lay in zone_lbls:
                out_msp.add_text(txt[:50],dxfattribs={
                    "layer":lay,"color":256,
                    "insert":(mx+xc,my),"height":h,"rotation":txt_rot})
                placed_labels+=1

    # ── RCP ───────────────────────────────────────────────────────────────────
    if doc_m is not doc_a1:
        del doc_m
    del doc_a1, msp_m
    gc.collect()

    RCP_SKIP_LAYERS={
        "A-WALL","A-WALL-PATT","A-FLOR",
        "A-GLAZ-CURT","A-GLAZ-CWMG","I-WALL",
        "A-ANNO-DIMS","A-ANNO-DIMS-96",
        "G-ANNO-NPLT","G-ANNO-TEXT",
        "G-ANNO-TTLB","G-ANNO-TTLB-WIDE","A-AREA-IDEN",
    }
    # Grid lines + bubbles: never fixture geometry, drop at leaf level
    # even inside ceiling blocks. INSERTs are still always traversed.
    RCP_GRID={"S-GRID","S-GRID-IDEN"}
    RCP_SKIP_SUB=("WALL","MURO","PARED","DOOR","PUERTA","WINDOW","VENTANA",
                  "FURN","MUEBLE","PISO","FLOOR","STAIR","ESCAL","CASEWORK",
                  "PLUMB","SANIT")
    def is_ceiling_layer(n):
        if n in RCP_SKIP_LAYERS or n in RCP_GRID: return False
        u=n.upper()
        if any(k in u for k in CEIL_LAYER_KW): return True   # always keep lights
        if any(k in u for k in RCP_SKIP_SUB): return False
        return True
    floor_cx=(min(z[0] for z in zones)+max(z[1] for z in zones))/2
    rcp_count=0
    rcp_names=[]

    # Floor-plan fingerprint for mirror detection: midpoints of everything
    # already drawn, snapped to a coarse grid.
    MG=50
    floor_keys=set()
    for e in out_msp:
        try:
            if e.dxftype()=="LINE":
                floor_keys.add((round((e.dxf.start.x+e.dxf.end.x)/2/MG),
                                round((e.dxf.start.y+e.dxf.end.y)/2/MG)))
            elif e.dxftype()=="LWPOLYLINE":
                pts=list(e.get_points())
                for a,b in zip(pts,pts[1:]):
                    floor_keys.add((round((a[0]+b[0])/2/MG),round((a[1]+b[1])/2/MG)))
        except: pass

    # Floor wall bbox (for aligning an RCP drawn somewhere else)
    def _bbox(pts):
        xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
        return (min(xs),max(xs),min(ys),max(ys)) if pts else None
    _fw=[]
    for e in out_msp:
        try:
            if e.dxftype()=="LINE" and e.dxf.layer=="A-WALL":
                _fw.append(((e.dxf.start.x+e.dxf.end.x)/2,(e.dxf.start.y+e.dxf.end.y)/2))
        except: pass
    fbox=_bbox(_fw) if len(_fw)>=20 else (min(z[0] for z in zones),max(z[1] for z in zones),
                                          min(z[2] for z in zones),max(z[3] for z in zones))
    def _overlap(a,b):
        if not a or not b: return 0.0
        ix=max(0,min(a[1],b[1])-max(a[0],b[0])); iy=max(0,min(a[3],b[3])-max(a[2],b[2]))
        aa=(a[1]-a[0])*(a[3]-a[2]); ab=(b[1]-b[0])*(b[3]-b[2])
        return (ix*iy)/max(1e-9,min(aa,ab))
    _WALLKW=("WALL","MURO","PARED")

    for rcp_path in rcp_paths:
        try:
            doc_rcp=ezdxf.readfile(str(rcp_path))
            msp_rcp=doc_rcp.modelspace()
            src_doc,src_msp,src_name=doc_rcp,msp_rcp,"self"

            # 1. Is this sheet xref'ing its ceiling from another uploaded file?
            rx=None
            for e in msp_rcp:
                try:
                    if e.dxftype()=="INSERT" and e.dxf.name not in SKIP_BLOCKS:
                        nm=e.dxf.name.lower()
                        if nm in dxf_stems and nm!=rcp_path.stem.lower():
                            rx=(nm,e.dxf.insert.x,e.dxf.insert.y,
                                getattr(e.dxf,'xscale',1.0),getattr(e.dxf,'yscale',1.0),
                                math.radians(getattr(e.dxf,'rotation',0.0)))
                            break
                except: pass
            def sheet_to_src(ax,ay):
                if not rx: return ax,ay
                _,ix_,iy_,sx_,sy_,rot_=rx
                dx=ax-ix_; dy=ay-iy_
                cr=math.cos(-rot_); sr=math.sin(-rot_)
                return (dx*cr-dy*sr)/sx_,(dx*sr+dy*cr)/sy_
            if rx:
                p_src=next((p for p in dxf_paths if p.stem.lower()==rx[0]),None)
                if p_src:
                    src_doc=ezdxf.readfile(str(p_src)); src_msp=src_doc.modelspace(); src_name=p_src.name

            # 2. RCP's own viewport zones, in SOURCE coords (None = unrestricted)
            rcp_zones=[]
            for layout in doc_rcp.layouts:
                if layout.name=="Model": continue
                for e in layout:
                    try:
                        if e.dxftype()=="VIEWPORT":
                            vcp=getattr(e.dxf,'view_center_point',None)
                            vh=getattr(e.dxf,'view_height',None)
                            ps_w=getattr(e.dxf,'width',None); ps_h=getattr(e.dxf,'height',None)
                            if vcp and vh and vh>0:
                                mx,my=sheet_to_src(vcp.x,vcp.y)
                                half_h=vh/2
                                aspect=(ps_w/ps_h) if (ps_w and ps_h and ps_h>0) else 1.5
                                half_w=half_h*aspect
                                if half_w<50 or half_h<50: continue
                                rcp_zones.append((mx-half_w,mx+half_w,my-half_h,my+half_h))
                    except: pass
            _insrc=(lambda x,y: any(X1<=x<=X2 and Y1<=y<=Y2 for X1,X2,Y1,Y2 in rcp_zones)) if rcp_zones else (lambda x,y: True)

            # 3. Sample the RCP geometry (raw source coords) + which are walls
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

            # 4. Translate if the RCP sits somewhere else than the plan
            tx=ty=0.0
            if rbox and _overlap(rbox,fbox)<0.3:
                tx=(fbox[0]+fbox[1])/2-(rbox[0]+rbox[1])/2
                ty=(fbox[2]+fbox[3])/2-(rbox[2]+rbox[3])/2

            # 5. Mirror vs aligned, scored on the translated points
            score_mirror=sum((round((2*floor_cx-(x+tx))/MG),round((y+ty)/MG)) in floor_keys for x,y,_ in _all)
            score_align =sum((round((x+tx)/MG),round((y+ty)/MG)) in floor_keys for x,y,_ in _all)
            # Trust the better match; ties keep the mirror default (Revit)
            use_mirror = not (score_align>=5 and score_align>score_mirror)
            def T(x,y):
                x2=x+tx; y2=y+ty
                return ((2*floor_cx-x2) if use_mirror else x2), y2

            # 6. Extract ceiling leaves, transform at the leaf
            rcp_ec=[0]; RA={"layer":"A-RCP","color":256}
            for ve,in_ceil in iter_leaves(src_msp,src_doc,5,ceil_fn=is_ceiling_layer,skip=set(SKIP)):
                if rcp_ec[0]>60000: break
                try:
                    lay=getattr(ve.dxf,'layer','0')
                    if lay in RCP_GRID: continue
                    if not in_ceil and not is_ceiling_layer(lay): continue
                    g=leaf_geom(ve,flat)
                    if g is None: continue
                    m=geom_mid(g)
                    if not (_insrc(*m) and inzone(*T(*m))): continue
                    write_leaf(out_msp,g,RA,PT=T,reflect=use_mirror)
                    rcp_ec[0]+=1
                except: pass
            rcp_count+=rcp_ec[0]
            rcp_names.append(
                f"{rcp_path.name} [src={src_name}, vp={len(rcp_zones)}, pts={len(_all)}, walls={len(_wpts)}, "
                f"shift=({tx:.0f},{ty:.0f}), align={score_align}/mirror={score_mirror} → "
                f"{'mirrored' if use_mirror else 'aligned'}, added={rcp_ec[0]}]")
            if src_doc is not doc_rcp: del src_doc
            del doc_rcp
            gc.collect()
        except Exception as ex:
            rcp_names.append(f"{rcp_path.name} [ERROR: {ex}]")

    # ── PROJECT STRUCTURE ────────────────────────────────────────────────────
    stems=[p.stem for p in dxf_paths]
    pre=os.path.commonprefix(stems).strip(" -_")
    project=pre if len(pre)>=4 else max(stems,key=len)
    sheets=build_project(out,zones,ins_units,project,has_rcp=bool(rcp_names))

    out_path=tmp/"floor_plan_clean.dxf"
    out.saveas(str(out_path))
    dxf_bytes=out_path.read_bytes()
    dwg_path=dxf_to_dwg(out_path)
    dwg_bytes=dwg_path.read_bytes() if dwg_path else None
    msg=f"Done. {ec[0]+copied} entities, {placed_labels} labels"
    if not dwg_bytes: msg+=" (DWG conversion failed — DXF only)"
    if debug_info: msg+=" | "+" | ".join(debug_info)
    if rcp_names: msg+=f", RCP on layer A-RCP from {'; '.join(rcp_names)}"
    msg+=f". Sheets: {', '.join(sheets)}"
    return (dxf_bytes,dwg_bytes),msg+".",None

# ── UI ────────────────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages=[{"role":"assistant","content":"Upload your DWG or DXF files below, then click Process when ready."}]
if "result" not in st.session_state:
    st.session_state.result=None
if "processed_files" not in st.session_state:
    st.session_state.processed_files=set()

st.markdown("<h4 style='text-align:center; padding: 20px 0 10px;'>NEC Placer</h4>",unsafe_allow_html=True)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

if st.session_state.result:
    dxf_bytes,dwg_bytes=st.session_state.result
    with st.chat_message("assistant"):
        c1,c2=st.columns(2)
        if dwg_bytes:
            with c1:
                st.download_button("Download floor_plan_clean.dwg",
                    data=dwg_bytes,file_name="floor_plan_clean.dwg",
                    mime="application/octet-stream",type="primary",
                    use_container_width=True)
        with c2:
            st.download_button("Download floor_plan_clean.dxf",
                data=dxf_bytes,file_name="floor_plan_clean.dxf",
                mime="application/octet-stream",use_container_width=True)

uploaded=st.file_uploader("Upload files",type=["dxf","dwg"],
    accept_multiple_files=True,label_visibility="collapsed")

if uploaded:
    col1,col2=st.columns([3,1])
    with col2:
        process_btn=st.button("Process",use_container_width=True,type="primary")
    if process_btn:
        file_key=frozenset(f.name for f in uploaded)
        if file_key not in st.session_state.processed_files:
            st.session_state.processed_files.add(file_key)
            names=", ".join(f.name for f in uploaded)
            st.session_state.messages.append({"role":"user","content":f"Uploaded: {names}"})
            with st.chat_message("assistant"):
                with st.spinner("Processing..."):
                    result,msg,error=process_files(uploaded)
                    if error:
                        st.session_state.messages.append({"role":"assistant","content":error})
                    elif result:
                        st.session_state.result=result
                        st.session_state.messages.append({"role":"assistant","content":msg})
                    else:
                        st.session_state.messages.append({"role":"assistant","content":msg})
            st.rerun()
