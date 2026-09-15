import streamlit as st
import ezdxf
import math
import re
import tempfile
import subprocess
import os
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

# ── AUTH ──────────────────────────────────────────────────────────────────────
if not st.session_state.get("authenticated"):
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.markdown("### NEC Placer")
        pw = st.text_input("Password", type="password",
                           label_visibility="collapsed", placeholder="Password")
        if st.button("Continue", use_container_width=True):
            if pw == os.environ.get("password", "necplacer2025"):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password")
    st.stop()

# ── DWG CONVERSION ────────────────────────────────────────────────────────────
def dwg_to_dxf(dwg_path: Path) -> Path:
    try:
        subprocess.run(["dwg2dxf", str(dwg_path)],
                       capture_output=True, timeout=60)
        dxf_path = dwg_path.with_suffix(".dxf")
        if dxf_path.exists():
            return dxf_path
    except: pass
    return None

# ── CONFIG ────────────────────────────────────────────────────────────────────
MAX_ENTITIES = 80000

SKIP = {"EXIST-SPOT-ELEV","Surface_CONTOUR","Surface_CONTOUR_TAG",
        "Surface_CONTOUR_IDX","Surface_CONTOUR_MID","SECTTAG",
        "site-info","SECCION-LINE","PROPERTY LIMT","_NATURAL",
        "north","SITE","TABLE","TABLEDATA","TABLELN",
        "AP-FLOOR LIMIT","AP-ROOF OVERHANG","AP-CONCRETE PAD",
        "AP-CURB","AP-CONCRETE BENCH","EXIST-WATER-METER",
        "EXIST-MANHOLE","LG","AP-EXISTING GRAGE LINE",
        "AP-HATCH GLASS","A-HATCH EARTH","AP-STONE HATCH",
        "AP-SOLID","AP-DIM","TXT-2","TXT-3","TXT-4","NORTE",
        "HIDDEN","CENTER","SECTTAG","TABLELN","AP-4","AP-3",
        "AP-2","AP-1","AP-5","AP-6","AP-8"}

SKIP_BLOCKS = {"*","AME_NIL","AME_SOL","FLECHA-X","2-TIT360",
               "ELE1","ELE2","ELE3","ELE4","SECT-1","SECT-2",
               "SECT-3","SECT-4","AVE_RENDER"}

def clean_mtext(txt):
    txt = re.sub(r'\\f[^;]+;','',txt)
    txt = re.sub(r'\\[A-Za-z][^;]*;','',txt)
    txt = re.sub(r'\{|\}','',txt)
    txt = re.sub(r'\\U\+[0-9A-Fa-f]+','',txt)
    return txt.replace('\\P',' ').replace('\\~',' ').strip()

def sheet_score(name):
    n = name.lower()
    if any(k in n for k in ["floor plan","ground","a101","a-1 ","a1 "]): return 2
    if any(k in n for k in ["site","rcp","roof","ceiling"]): return 0
    return 1

def is_crossing_line(x1,y1,x2,y2):
    length = math.hypot(x2-x1,y2-y1)
    if length < 1000: return False
    dx,dy = abs(x2-x1),abs(y2-y1)
    return dx<50 or dy<50

def process_files(uploaded_files):
    tmp = Path(tempfile.mkdtemp())
    dxf_paths = []
    conversion_errors = []

    for f in uploaded_files:
        raw = f.read()
        p = tmp / f.name
        p.write_bytes(raw)
        if f.name.lower().endswith(".dwg"):
            st.write(f"Converting {f.name}...")
            converted = dwg_to_dxf(p)
            if converted:
                dxf_paths.append(converted)
                st.write(f"✓ Converted")
            else:
                conversion_errors.append(f.name)
        else:
            dxf_paths.append(p)

    if conversion_errors and not dxf_paths:
        return None, None, f"Could not convert: {', '.join(conversion_errors)}"
    if not dxf_paths:
        return None, None, "No readable files found."

    file_info = []
    for p in dxf_paths:
        try:
            doc = ezdxf.readfile(str(p))
            has_vp = any(e.dxftype()=="VIEWPORT"
                         for layout in doc.layouts if layout.name!="Model"
                         for e in layout)
            walls = sum(1 for e in doc.modelspace()
                        if e.dxftype()=="LINE" and
                        getattr(e.dxf,'layer','') in
                        ["AP-WALL","AR-WALLS","A-WALL","WALL","Walls"])
            file_info.append((p,doc,has_vp,walls))
        except: pass

    if not file_info:
        return None, None, "Could not read any files."

    candidates = [(f,d,w) for f,d,vp,w in file_info if vp]
    if not candidates:
        candidates = [(f,d,w) for f,d,vp,w in file_info]
    sheet_path, doc_a1, _ = max(candidates, key=lambda x: sheet_score(x[0].name))

    dxf_stems = {p.stem.lower() for p in dxf_paths}
    xref_name = None
    xref_ix = xref_iy = 0.0
    xref_sx = xref_sy = 1.0
    xref_rot = 0.0

    for e in doc_a1.modelspace():
        try:
            if e.dxftype()=="INSERT" and e.dxf.name not in SKIP_BLOCKS:
                if e.dxf.name.lower() in dxf_stems:
                    xref_name = e.dxf.name
                    xref_ix = e.dxf.insert.x
                    xref_iy = e.dxf.insert.y
                    xref_sx = getattr(e.dxf,'xscale',1.0)
                    xref_sy = getattr(e.dxf,'yscale',1.0)
                    xref_rot = math.radians(getattr(e.dxf,'rotation',0.0))
                    break
        except: pass

    if xref_name:
        master_path = next((p for p in dxf_paths
                            if p.stem.lower()==xref_name.lower()), None)
        doc_m = next((d for p,d,_,_ in file_info if p==master_path), doc_a1)
    else:
        doc_m = doc_a1

    msp_m = doc_m.modelspace()

    def a1_to_master(ax, ay):
        dx=ax-xref_ix; dy=ay-xref_iy
        cr=math.cos(-xref_rot); sr=math.sin(-xref_rot)
        ux=dx*cr-dy*sr; uy=dx*sr+dy*cr
        return ux/xref_sx, uy/xref_sy

    # ── Viewport zones ────────────────────────────────────────────────────────
    zones = []
    for layout in doc_a1.layouts:
        if layout.name=="Model": continue
        for e in layout:
            try:
                if e.dxftype()=="VIEWPORT":
                    vcp = getattr(e.dxf,'view_center_point',None)
                    vh  = getattr(e.dxf,'view_height',None)
                    ps_w = getattr(e.dxf,'width',None)
                    ps_h = getattr(e.dxf,'height',None)
                    if vcp and vh and vh>0:
                        mx,my = a1_to_master(vcp.x,vcp.y) if xref_name else (vcp.x,vcp.y)
                        half_h = vh/2
                        aspect = (ps_w/ps_h) if (ps_w and ps_h and ps_h>0) else 1.5
                        half_w = half_h*aspect
                        if half_w<50 or half_h<50: continue
                        zones.append((mx-half_w,mx+half_w,my-half_h,my+half_h))
            except: pass

    # ── Wall cluster zones (for floors not in viewports) ──────────────────────
    wall_pts = []
    for e in msp_m:
        try:
            if e.dxftype()=="LINE" and getattr(e.dxf,'layer','') in \
               ["AP-WALL","AR-WALLS","A-WALL","WALL","Walls"]:
                cx=(e.dxf.start.x+e.dxf.end.x)/2
                cy=(e.dxf.start.y+e.dxf.end.y)/2
                wall_pts.append((cx,cy))
        except: pass

    if wall_pts:
        xs = sorted(x for x,y in wall_pts)
        med_x = xs[len(xs)//2]
        lot_x1 = med_x - 1500
        lot_x2 = med_x + 1500
        ys_in_lot = [y for x,y in wall_pts if lot_x1<=x<=lot_x2]
        if ys_in_lot:
            bands = {}
            for y in ys_in_lot:
                b = round(y/500)*500
                bands[b] = bands.get(b,0)+1
            sorted_b = sorted(bands.keys())
            clusters,cur = [],[sorted_b[0]]
            for i in range(1,len(sorted_b)):
                if sorted_b[i]-sorted_b[i-1]<=1000: cur.append(sorted_b[i])
                else: clusters.append(cur); cur=[sorted_b[i]]
            clusters.append(cur)
            for count,cluster in sorted([(sum(bands.get(b,0) for b in c),c)
                                          for c in clusters],reverse=True)[:4]:
                if count < 20: continue
                y1,y2 = min(cluster)-300, max(cluster)+300
                if not any(Z1<=(y1+y2)/2<=Z2 for _,_,Z1,Z2 in zones):
                    zones.append((lot_x1,lot_x2,y1,y2))

    if not zones:
        zones=[(-1e9,1e9,-1e9,1e9)]

    # ── Extract ───────────────────────────────────────────────────────────────
    out = ezdxf.new("R2018")
    out_msp = out.modelspace()
    entity_count = [0]
    visited_blocks = set()

    def explode(blk_name, ix, iy, sx, sy, rot, depth=0):
        if depth>3 or entity_count[0]>MAX_ENTITIES: return
        if blk_name in visited_blocks: return
        if depth==0: visited_blocks.add(blk_name)
        if blk_name not in doc_m.blocks: return
        cr,sr=math.cos(rot),math.sin(rot)
        def xf(px,py):
            lx,ly=px*sx,py*sy
            return ix+lx*cr-ly*sr, iy+lx*sr+ly*cr
        for be in doc_m.blocks[blk_name]:
            if entity_count[0]>MAX_ENTITIES: break
            try:
                bl=getattr(be.dxf,'layer','0')
                if bl in SKIP: continue
                bt=be.dxftype()
                if bt=="LINE":
                    p1=xf(be.dxf.start.x,be.dxf.start.y)
                    p2=xf(be.dxf.end.x,be.dxf.end.y)
                    if not is_crossing_line(p1[0],p1[1],p2[0],p2[1]):
                        out_msp.add_line(p1,p2,dxfattribs={"layer":bl,"color":8})
                        entity_count[0]+=1
                elif bt=="LWPOLYLINE":
                    pts=list(be.get_points())
                    if pts:
                        out_msp.add_lwpolyline([xf(p[0],p[1]) for p in pts],
                            dxfattribs={"layer":bl,"color":8,"closed":be.is_closed})
                        entity_count[0]+=1
                elif bt=="ARC":
                    nc=xf(be.dxf.center.x,be.dxf.center.y)
                    out_msp.add_arc(center=nc,radius=be.dxf.radius*sx,
                        start_angle=be.dxf.start_angle+math.degrees(rot),
                        end_angle=be.dxf.end_angle+math.degrees(rot),
                        dxfattribs={"layer":bl,"color":8})
                    entity_count[0]+=1
                elif bt=="CIRCLE":
                    nc=xf(be.dxf.center.x,be.dxf.center.y)
                    out_msp.add_circle(center=nc,radius=be.dxf.radius*sx,
                        dxfattribs={"layer":bl,"color":8})
                    entity_count[0]+=1
                elif bt=="SPLINE":
                    spts=list(be.control_points)
                    if spts:
                        out_msp.add_lwpolyline([xf(p[0],p[1]) for p in spts],
                            dxfattribs={"layer":bl,"color":8})
                        entity_count[0]+=1
                elif bt=="INSERT":
                    nix,niy=xf(be.dxf.insert.x,be.dxf.insert.y)
                    explode(be.dxf.name,nix,niy,
                        sx*getattr(be.dxf,'xscale',1.0),
                        sy*getattr(be.dxf,'yscale',1.0),
                        rot+math.radians(getattr(be.dxf,'rotation',0.0)),depth+1)
            except: pass

    for e in msp_m:
        if entity_count[0]>MAX_ENTITIES: break
        try:
            layer=getattr(e.dxf,'layer','0')
            if layer in SKIP: continue
            t=e.dxftype()
            for (X1,X2,Y1,Y2) in zones:
                placed=False
                if t=="LINE":
                    cx=(e.dxf.start.x+e.dxf.end.x)/2
                    cy=(e.dxf.start.y+e.dxf.end.y)/2
                    if X1<=cx<=X2 and Y1<=cy<=Y2:
                        x1,y1_=e.dxf.start.x,e.dxf.start.y
                        x2,y2_=e.dxf.end.x,e.dxf.end.y
                        if not is_crossing_line(x1,y1_,x2,y2_):
                            out_msp.add_line((x1,y1_),(x2,y2_),
                                dxfattribs={"layer":layer,"color":8})
                            entity_count[0]+=1
                        placed=True
                elif t=="LWPOLYLINE":
                    pts=list(e.get_points())
                    if pts:
                        cx=sum(p[0] for p in pts)/len(pts)
                        cy=sum(p[1] for p in pts)/len(pts)
                        if X1<=cx<=X2 and Y1<=cy<=Y2:
                            out_msp.add_lwpolyline([(p[0],p[1]) for p in pts],
                                dxfattribs={"layer":layer,"color":8,"closed":e.is_closed})
                            entity_count[0]+=1
                            placed=True
                elif t=="ARC":
                    cx,cy=e.dxf.center.x,e.dxf.center.y
                    if X1<=cx<=X2 and Y1<=cy<=Y2:
                        out_msp.add_arc(center=(cx,cy),radius=e.dxf.radius,
                            start_angle=e.dxf.start_angle,end_angle=e.dxf.end_angle,
                            dxfattribs={"layer":layer,"color":8})
                        entity_count[0]+=1
                        placed=True
                elif t=="CIRCLE":
                    cx,cy=e.dxf.center.x,e.dxf.center.y
                    if X1<=cx<=X2 and Y1<=cy<=Y2:
                        out_msp.add_circle(center=(cx,cy),radius=e.dxf.radius,
                            dxfattribs={"layer":layer,"color":8})
                        entity_count[0]+=1
                        placed=True
                elif t=="SPLINE":
                    pts=list(e.control_points)
                    if pts:
                        cx=sum(p[0] for p in pts)/len(pts)
                        cy=sum(p[1] for p in pts)/len(pts)
                        if X1<=cx<=X2 and Y1<=cy<=Y2:
                            out_msp.add_lwpolyline([(p[0],p[1]) for p in pts],
                                dxfattribs={"layer":layer,"color":8})
                            entity_count[0]+=1
                            placed=True
                elif t=="INSERT":
                    cx,cy=e.dxf.insert.x,e.dxf.insert.y
                    if X1<=cx<=X2 and Y1<=cy<=Y2:
                        visited_blocks.clear()
                        explode(e.dxf.name,cx,cy,
                            getattr(e.dxf,'xscale',1.0),
                            getattr(e.dxf,'yscale',1.0),
                            math.radians(getattr(e.dxf,'rotation',0.0)))
                        placed=True
                if placed: break
        except: pass

    # ── Labels ────────────────────────────────────────────────────────────────
    placed_labels=0
    for e in doc_a1.modelspace():
        try:
            if e.dxftype() in ["TEXT","MTEXT"]:
                if e.dxftype()=="TEXT":
                    txt=e.dxf.text.strip()
                    ix,iy=e.dxf.insert.x,e.dxf.insert.y
                    txt_rot=getattr(e.dxf,'rotation',0.0)
                    h=e.dxf.height
                else:
                    txt=clean_mtext(e.text)
                    ix,iy=e.dxf.insert.x,e.dxf.insert.y
                    txt_rot=getattr(e.dxf,'rotation',0.0)
                    h=getattr(e.dxf,'char_height',20)
                if len(txt)<2: continue
                if xref_name:
                    mx,my=a1_to_master(ix,iy)
                    txt_rot=txt_rot-math.degrees(xref_rot)
                else:
                    mx,my=ix,iy
                for (X1,X2,Y1,Y2) in zones:
                    if X1<=mx<=X2 and Y1<=my<=Y2:
                        out_msp.add_text(txt[:50],dxfattribs={
                            "layer":"ROOM-LABELS","color":253,
                            "insert":(mx,my),"height":h*xref_sx,
                            "rotation":txt_rot})
                        placed_labels+=1
                        break
        except: pass

    out_path = tmp / "floor_plan_clean.dxf"
    out.saveas(str(out_path))
    return out_path.read_bytes(), f"Done. {entity_count[0]} entities, {placed_labels} labels.", None

# ── CHAT UI ───────────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "Upload your DWG or DXF files below, then click Process when ready."}
    ]
if "result" not in st.session_state:
    st.session_state.result = None
if "processed_files" not in st.session_state:
    st.session_state.processed_files = set()

st.markdown("<h4 style='text-align:center; padding: 20px 0 10px;'>NEC Placer</h4>",
            unsafe_allow_html=True)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

if st.session_state.result:
    with st.chat_message("assistant"):
        st.download_button(
            "Download floor_plan_clean.dxf",
            data=st.session_state.result,
            file_name="floor_plan_clean.dxf",
            mime="application/octet-stream"
        )

uploaded = st.file_uploader(
    "Upload files",
    type=["dxf","dwg"],
    accept_multiple_files=True,
    label_visibility="collapsed"
)

if uploaded:
    col1, col2 = st.columns([3,1])
    with col2:
        process_btn = st.button("Process", use_container_width=True, type="primary")
    if process_btn:
        file_key = frozenset(f.name for f in uploaded)
        if file_key not in st.session_state.processed_files:
            st.session_state.processed_files.add(file_key)
            names = ", ".join(f.name for f in uploaded)
            st.session_state.messages.append({"role":"user","content":f"Uploaded: {names}"})
            with st.chat_message("assistant"):
                with st.spinner("Processing..."):
                    result, msg, error = process_files(uploaded)
                    if error:
                        st.session_state.messages.append({"role":"assistant","content":error})
                    elif result:
                        st.session_state.result = result
                        st.session_state.messages.append({"role":"assistant","content":msg})
                    else:
                        st.session_state.messages.append({"role":"assistant","content":msg})
            st.rerun()
