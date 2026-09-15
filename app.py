import streamlit as st
import ezdxf
import math
import re
import tempfile
from pathlib import Path

st.set_page_config(page_title="NEC Placer", page_icon="⚡", layout="wide")

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background: #0e0e0e; }
    .block-container { padding-top: 0 !important; }
    h1 { color: white !important; }
    .stTextInput input { background: #1a1a1a; color: white; border: 1px solid #333; }
    .stButton button {
        background: #e8c84a; color: black; font-weight: 800;
        border: none; border-radius: 6px; width: 100%;
    }
    .stButton button:hover { background: #f0d060; }
    .msg-user {
        background: #1a1a1a; border-radius: 12px; padding: 12px 16px;
        margin: 8px 0; color: #ccc; text-align: right;
    }
    .msg-ai {
        background: #111; border-left: 3px solid #e8c84a;
        border-radius: 8px; padding: 12px 16px; margin: 8px 0; color: #ddd;
    }
    .badge {
        display: inline-block; background: #e8c84a22; color: #e8c84a;
        border: 1px solid #e8c84a44; border-radius: 20px;
        padding: 2px 12px; font-size: 12px; margin: 2px;
    }
</style>
""", unsafe_allow_html=True)

# ── AUTH ──────────────────────────────────────────────────────────────────────
def check_password():
    if st.session_state.get("authenticated"):
        return True

    st.markdown("""
    <div style='text-align:center; padding: 80px 20px 30px'>
        <div style='font-size:48px'>⚡</div>
        <h1 style='font-size:64px; font-weight:900; letter-spacing:-3px;
                   background: linear-gradient(135deg,#e8c84a,#fff);
                   -webkit-background-clip:text; -webkit-text-fill-color:transparent;
                   margin:0'>NEC PLACER</h1>
        <p style='color:#888; font-size:16px; max-width:480px; margin:16px auto 0'>
            AI-powered electrical plan preparation for Puerto Rico.<br>
            Upload your architectural files. Get a clean canvas, instantly.
        </p>
        <div style='margin-top:16px'>
            <span class='badge'>⚠️ Prototype</span>
            <span class='badge'>Canvas cleanup only</span>
            <span class='badge'>Receptacle placement coming soon</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1.5, 1, 1.5])
    with col2:
        pw = st.text_input("", type="password", placeholder="Password",
                           label_visibility="collapsed")
        if st.button("Enter ⚡", use_container_width=True):
            if pw == st.secrets.get("password", "necplacer2025"):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Wrong password")
    return False

if not check_password():
    st.stop()

# ── EXTRACT LOGIC ─────────────────────────────────────────────────────────────
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

def sheet_score(name):
    n = name.lower()
    if any(k in n for k in ["floor plan","ground","a101","a-1 ","a1 "]): return 2
    if any(k in n for k in ["site","rcp","roof","ceiling"]): return 0
    return 1

def process_files(uploaded_files, status):
    tmp = Path(tempfile.mkdtemp())
    dxf_paths = []

    status.write("💾 Saving uploaded files...")
    for f in uploaded_files:
        p = tmp / f.name
        p.write_bytes(f.read())
        dxf_paths.append(p)

    status.write("🔍 Analyzing files...")
    file_info = []
    for p in dxf_paths:
        try:
            doc = ezdxf.readfile(str(p))
            has_vp = any(
                e.dxftype()=="VIEWPORT"
                for layout in doc.layouts if layout.name!="Model"
                for e in layout
            )
            walls = sum(1 for e in doc.modelspace()
                        if e.dxftype()=="LINE" and
                        getattr(e.dxf,'layer','') in
                        ["AP-WALL","AR-WALLS","A-WALL","WALL","Walls"])
            texts = sum(1 for e in doc.modelspace()
                        if e.dxftype() in ["TEXT","MTEXT"])
            file_info.append((p,doc,has_vp,walls,texts))
        except: pass

    if not file_info:
        return None, "No readable DXF files found."

    # Pick sheet
    candidates = [(f,d,w,t) for f,d,vp,w,t in file_info if vp]
    if not candidates:
        candidates = [(f,d,w,t) for f,d,vp,w,t in file_info]
    sheet_path, doc_a1, _, _ = max(candidates, key=lambda x: sheet_score(x[0].name))

    status.write(f"📄 Sheet: `{sheet_path.name}`")

    # Detect xref
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
        master_path = next((p for p in dxf_paths if p.stem.lower()==xref_name.lower()), None)
        doc_m = next((d for p,d,_,_,_ in file_info if p==master_path), doc_a1)
        status.write(f"🔗 Traditional xref: `{xref_name}`")
    else:
        doc_m = doc_a1
        status.write("📦 Revit/embedded structure detected")

    msp_m = doc_m.modelspace()

    def a1_to_master(ax, ay):
        dx=ax-xref_ix; dy=ay-xref_iy
        cr=math.cos(-xref_rot); sr=math.sin(-xref_rot)
        ux=dx*cr-dy*sr; uy=dx*sr+dy*cr
        return ux/xref_sx, uy/xref_sy

    # Read viewports
    status.write("🗺️ Reading paper space viewports...")
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
                        if xref_name:
                            mx,my = a1_to_master(vcp.x,vcp.y)
                        else:
                            mx,my = vcp.x,vcp.y
                        half_h = vh/2
                        aspect = (ps_w/ps_h) if (ps_w and ps_h and ps_h>0) else 1.5
                        half_w = half_h*aspect
                        if half_w<50 or half_h<50: continue
                        zones.append((mx-half_w,mx+half_w,my-half_h,my+half_h))
            except: pass

    status.write(f"✅ Found {len(zones)} floor plan zones")
    if not zones:
        zones=[(-1e9,1e9,-1e9,1e9)]

    # Extract
    status.write("⚙️ Extracting geometry...")
    out = ezdxf.new("R2018")
    out_msp = out.modelspace()
    copied = 0

    def explode(blk_name, ix, iy, sx, sy, rot, depth=0):
        if depth>5: return
        if blk_name not in doc_m.blocks: return
        cr,sr=math.cos(rot),math.sin(rot)
        def xf(px,py):
            lx,ly=px*sx,py*sy
            return ix+lx*cr-ly*sr, iy+lx*sr+ly*cr
        for be in doc_m.blocks[blk_name]:
            try:
                bl=getattr(be.dxf,'layer','0')
                if bl in SKIP: continue
                bt=be.dxftype()
                if bt=="LINE":
                    out_msp.add_line(xf(be.dxf.start.x,be.dxf.start.y),
                        xf(be.dxf.end.x,be.dxf.end.y),
                        dxfattribs={"layer":bl,"color":8})
                elif bt=="LWPOLYLINE":
                    pts=list(be.get_points())
                    if pts:
                        out_msp.add_lwpolyline([xf(p[0],p[1]) for p in pts],
                            dxfattribs={"layer":bl,"color":8,"closed":be.is_closed})
                elif bt=="ARC":
                    nc=xf(be.dxf.center.x,be.dxf.center.y)
                    out_msp.add_arc(center=nc,radius=be.dxf.radius*sx,
                        start_angle=be.dxf.start_angle+math.degrees(rot),
                        end_angle=be.dxf.end_angle+math.degrees(rot),
                        dxfattribs={"layer":bl,"color":8})
                elif bt=="CIRCLE":
                    nc=xf(be.dxf.center.x,be.dxf.center.y)
                    out_msp.add_circle(center=nc,radius=be.dxf.radius*sx,
                        dxfattribs={"layer":bl,"color":8})
                elif bt=="SPLINE":
                    spts=list(be.control_points)
                    if spts:
                        out_msp.add_lwpolyline([xf(p[0],p[1]) for p in spts],
                            dxfattribs={"layer":bl,"color":8})
                elif bt=="INSERT":
                    nix,niy=xf(be.dxf.insert.x,be.dxf.insert.y)
                    explode(be.dxf.name,nix,niy,
                        sx*getattr(be.dxf,'xscale',1.0),
                        sy*getattr(be.dxf,'yscale',1.0),
                        rot+math.radians(getattr(be.dxf,'rotation',0.0)),depth+1)
            except: pass

    for e in msp_m:
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
                        out_msp.add_line((e.dxf.start.x,e.dxf.start.y),
                            (e.dxf.end.x,e.dxf.end.y),
                            dxfattribs={"layer":layer,"color":8})
                        placed=True
                elif t=="LWPOLYLINE":
                    pts=list(e.get_points())
                    if pts:
                        cx=sum(p[0] for p in pts)/len(pts)
                        cy=sum(p[1] for p in pts)/len(pts)
                        if X1<=cx<=X2 and Y1<=cy<=Y2:
                            out_msp.add_lwpolyline([(p[0],p[1]) for p in pts],
                                dxfattribs={"layer":layer,"color":8,"closed":e.is_closed})
                            placed=True
                elif t=="ARC":
                    cx,cy=e.dxf.center.x,e.dxf.center.y
                    if X1<=cx<=X2 and Y1<=cy<=Y2:
                        out_msp.add_arc(center=(cx,cy),radius=e.dxf.radius,
                            start_angle=e.dxf.start_angle,end_angle=e.dxf.end_angle,
                            dxfattribs={"layer":layer,"color":8})
                        placed=True
                elif t=="CIRCLE":
                    cx,cy=e.dxf.center.x,e.dxf.center.y
                    if X1<=cx<=X2 and Y1<=cy<=Y2:
                        out_msp.add_circle(center=(cx,cy),radius=e.dxf.radius,
                            dxfattribs={"layer":layer,"color":8})
                        placed=True
                elif t=="SPLINE":
                    pts=list(e.control_points)
                    if pts:
                        cx=sum(p[0] for p in pts)/len(pts)
                        cy=sum(p[1] for p in pts)/len(pts)
                        if X1<=cx<=X2 and Y1<=cy<=Y2:
                            out_msp.add_lwpolyline([(p[0],p[1]) for p in pts],
                                dxfattribs={"layer":layer,"color":8})
                            placed=True
                elif t=="INSERT":
                    cx,cy=e.dxf.insert.x,e.dxf.insert.y
                    if X1<=cx<=X2 and Y1<=cy<=Y2:
                        explode(e.dxf.name,cx,cy,
                            getattr(e.dxf,'xscale',1.0),
                            getattr(e.dxf,'yscale',1.0),
                            math.radians(getattr(e.dxf,'rotation',0.0)))
                        placed=True
                if placed:
                    copied+=1
                    break
        except: pass

    # Labels
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
                            "insert":(mx,my),"height":h*xref_sx,"rotation":txt_rot})
                        placed_labels+=1
                        break
        except: pass

    status.write(f"✅ {copied} entities + {placed_labels} labels extracted")

    # Save to temp file
    out_path = tmp / "floor_plan_clean.dxf"
    out.saveas(str(out_path))
    return out_path.read_bytes(), None

# ── MAIN UI ───────────────────────────────────────────────────────────────────
st.markdown("""
<div style='padding: 24px 0 8px'>
    <h1 style='font-size:36px; font-weight:900; margin:0'>⚡ NEC PLACER</h1>
    <p style='color:#666; margin:4px 0 0'>Campa, Cerrato & Associates — Internal Tool</p>
</div>
""", unsafe_allow_html=True)

if "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.messages.append({
        "role": "assistant",
        "content": "Hey! Upload your DXF files for a project and I'll clean them up into a ready-to-work canvas. Just drag and drop below."
    })

# Chat history
for msg in st.session_state.messages:
    if msg["role"] == "assistant":
        st.markdown(f"<div class='msg-ai'>⚡ {msg['content']}</div>",
                    unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='msg-user'>{msg['content']}</div>",
                    unsafe_allow_html=True)

# Upload
uploaded = st.file_uploader(
    "Drop your DXF files here",
    type=["dxf"],
    accept_multiple_files=True,
    label_visibility="collapsed"
)

if uploaded:
    names = ", ".join(f.name for f in uploaded)
    st.session_state.messages.append({
        "role": "user",
        "content": f"📁 Uploaded: {names}"
    })
    st.markdown(f"<div class='msg-user'>📁 {names}</div>", unsafe_allow_html=True)

    with st.status("Processing your files...", expanded=True) as status:
        result, error = process_files(uploaded, status)
        if error:
            status.update(label=f"❌ {error}", state="error")
            st.session_state.messages.append({"role":"assistant","content":f"❌ {error}"})
        else:
            status.update(label="✅ Done!", state="complete")
            msg = "Done! Your clean canvas is ready. Download it below and open in AutoCAD."
            st.session_state.messages.append({"role":"assistant","content":msg})
            st.markdown(f"<div class='msg-ai'>⚡ {msg}</div>", unsafe_allow_html=True)
            st.download_button(
                "⬇️ Download floor_plan_clean.dxf",
                data=result,
                file_name="floor_plan_clean.dxf",
                mime="application/octet-stream",
                use_container_width=True
            )
