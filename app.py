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

if not st.session_state.get("authenticated"):
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1,1,1])
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

def dwg_to_dxf(dwg_path: Path) -> Path:
    try:
        orig = os.getcwd()
        os.chdir(dwg_path.parent)
        subprocess.run(["dwg2dxf", dwg_path.name],
                       capture_output=True, timeout=60)
        os.chdir(orig)
        out = dwg_path.with_suffix(".dxf")
        if out.exists():
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

def sheet_score(name):
    n = name.lower()
    if any(k in n for k in ["floor plan","ground","a101","a-1 ","a1 "]): return 2
    if any(k in n for k in ["site","rcp","roof","ceiling","reflected"]): return 0
    return 1

def is_rcp(name):
    n = name.lower()
    return any(k in n for k in ["rcp","reflected ceiling","ceiling plan","a103"])

def quick_scan(p):
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
                     if e.dxftype()=="INSERT" and
                     e.dxf.name not in SKIP_BLOCKS), None)
        del doc
        return has_vp, walls, xref
    except:
        return False, 0, None

def get_viewport_zones(doc, xref_name=None, xref_ix=0, xref_iy=0,
                       xref_sx=1, xref_sy=1, xref_rot=0):
    def a1_to_master(ax, ay):
        dx=ax-xref_ix; dy=ay-xref_iy
        cr=math.cos(-xref_rot); sr=math.sin(-xref_rot)
        ux=dx*cr-dy*sr; uy=dx*sr+dy*cr
        return ux/xref_sx, uy/xref_sy

    zones = []
    for layout in doc.layouts:
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
    return zones

def extract_geometry(msp, zones, doc_blocks, out_msp, layer_color=8,
                     layer_prefix="", ec=None, max_ec=40000,
                     mirror_cx=None, xref_name=None,
                     all_x1=None, all_x2=None, all_y1=None, all_y2=None):
    """Extract geometry from msp into out_msp, optionally mirroring around cx"""
    if ec is None:
        ec = [0]

    use_bounds = all_x1 is not None

    def ib(cx,cy):
        if not use_bounds: return True
        return all_x1<=cx<=all_x2 and all_y1<=cy<=all_y2

    def mx_pt(x, y):
        if mirror_cx is None:
            return x, y
        return 2*mirror_cx - x, y

    def mirror_angle(a):
        """Mirror an angle around vertical axis"""
        return (180 - a) % 360

    def explode(bn, ix, iy, sx, sy, rot, d=0):
        depth_limit = 5 if xref_name else 3
        if d > depth_limit or ec[0] > max_ec: return
        if bn not in doc_blocks: return
        cr,sr=math.cos(rot),math.sin(rot)
        def xf(px,py):
            lx,ly=px*sx,py*sy
            rx=ix+lx*cr-ly*sr
            ry=iy+lx*sr+ly*cr
            return mx_pt(rx, ry)
        for be in doc_blocks[bn]:
            if ec[0] > max_ec: break
            try:
                bl = getattr(be.dxf,'layer','0')
                if bl in SKIP: continue
                bt = be.dxftype()
                if bt=="LINE":
                    p1=xf(be.dxf.start.x,be.dxf.start.y)
                    p2=xf(be.dxf.end.x,be.dxf.end.y)
                    if ib((p1[0]+p2[0])/2,(p1[1]+p2[1])/2):
                        out_msp.add_line(p1,p2,dxfattribs={"layer":bl,"color":layer_color})
                        ec[0]+=1
                elif bt=="LWPOLYLINE":
                    pts=list(be.get_points())
                    if pts:
                        tp=[xf(p[0],p[1]) for p in pts]
                        if ib(sum(p[0] for p in tp)/len(tp),sum(p[1] for p in tp)/len(tp)):
                            out_msp.add_lwpolyline(tp,dxfattribs={"layer":bl,"color":layer_color,"closed":be.is_closed})
                            ec[0]+=1
                elif bt=="ARC":
                    nc=xf(be.dxf.center.x,be.dxf.center.y)
                    if ib(nc[0],nc[1]):
                        if mirror_cx is not None:
                            sa=mirror_angle(be.dxf.end_angle)
                            ea=mirror_angle(be.dxf.start_angle)
                        else:
                            sa=be.dxf.start_angle+math.degrees(rot)
                            ea=be.dxf.end_angle+math.degrees(rot)
                        out_msp.add_arc(center=nc,radius=be.dxf.radius*sx,
                            start_angle=sa,end_angle=ea,
                            dxfattribs={"layer":bl,"color":layer_color})
                        ec[0]+=1
                elif bt=="CIRCLE":
                    nc=xf(be.dxf.center.x,be.dxf.center.y)
                    if ib(nc[0],nc[1]):
                        out_msp.add_circle(center=nc,radius=be.dxf.radius*sx,
                            dxfattribs={"layer":bl,"color":layer_color})
                        ec[0]+=1
                elif bt=="SPLINE":
                    sp=list(be.control_points)
                    if sp:
                        tp=[xf(p[0],p[1]) for p in sp]
                        if ib(sum(p[0] for p in tp)/len(tp),sum(p[1] for p in tp)/len(tp)):
                            out_msp.add_lwpolyline(tp,dxfattribs={"layer":bl,"color":layer_color})
                            ec[0]+=1
                elif bt=="INSERT":
                    ni,nj=xf(be.dxf.insert.x,be.dxf.insert.y)
                    if ib(ni,nj):
                        nr=math.radians(getattr(be.dxf,'rotation',0.0))
                        if mirror_cx is not None:
                            nr = math.pi - rot - nr
                        else:
                            nr = rot + nr
                        explode(be.dxf.name,ni,nj,
                            sx*getattr(be.dxf,'xscale',1.0),
                            sy*getattr(be.dxf,'yscale',1.0),nr,d+1)
            except: pass

    copied = 0
    for e in msp:
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
                        p1=mx_pt(e.dxf.start.x,e.dxf.start.y)
                        p2=mx_pt(e.dxf.end.x,e.dxf.end.y)
                        out_msp.add_line(p1,p2,dxfattribs={"layer":layer,"color":layer_color})
                        placed=True
                elif t=="LWPOLYLINE":
                    pts=list(e.get_points())
                    if pts:
                        cx=sum(p[0] for p in pts)/len(pts)
                        cy=sum(p[1] for p in pts)/len(pts)
                        if X1<=cx<=X2 and Y1<=cy<=Y2:
                            tp=[mx_pt(p[0],p[1]) for p in pts]
                            out_msp.add_lwpolyline(tp,dxfattribs={"layer":layer,"color":layer_color,"closed":e.is_closed})
                            placed=True
                elif t=="ARC":
                    cx,cy=e.dxf.center.x,e.dxf.center.y
                    if X1<=cx<=X2 and Y1<=cy<=Y2:
                        nc=mx_pt(cx,cy)
                        if mirror_cx is not None:
                            sa=mirror_angle(e.dxf.end_angle)
                            ea=mirror_angle(e.dxf.start_angle)
                        else:
                            sa=e.dxf.start_angle
                            ea=e.dxf.end_angle
                        out_msp.add_arc(center=nc,radius=e.dxf.radius,
                            start_angle=sa,end_angle=ea,
                            dxfattribs={"layer":layer,"color":layer_color})
                        placed=True
                elif t=="CIRCLE":
                    cx,cy=e.dxf.center.x,e.dxf.center.y
                    if X1<=cx<=X2 and Y1<=cy<=Y2:
                        out_msp.add_circle(center=mx_pt(cx,cy),radius=e.dxf.radius,
                            dxfattribs={"layer":layer,"color":layer_color})
                        placed=True
                elif t=="SPLINE":
                    pts=list(e.control_points)
                    if pts:
                        cx=sum(p[0] for p in pts)/len(pts)
                        cy=sum(p[1] for p in pts)/len(pts)
                        if X1<=cx<=X2 and Y1<=cy<=Y2:
                            tp=[mx_pt(p[0],p[1]) for p in pts]
                            out_msp.add_lwpolyline(tp,dxfattribs={"layer":layer,"color":layer_color})
                            placed=True
                elif t=="INSERT":
                    cx,cy=e.dxf.insert.x,e.dxf.insert.y
                    if X1<=cx<=X2 and Y1<=cy<=Y2:
                        rot=math.radians(getattr(e.dxf,'rotation',0.0))
                        if mirror_cx is not None:
                            rot = math.pi - rot
                        ni,nj=mx_pt(cx,cy)
                        explode(e.dxf.name,ni,nj,
                            getattr(e.dxf,'xscale',1.0),
                            getattr(e.dxf,'yscale',1.0),rot)
                        placed=True
                if placed:
                    copied+=1
                    break
        except: pass
    return copied

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

    # Scan all files
    dxf_stems = {p.stem.lower() for p in dxf_paths}
    file_meta = []
    for p in dxf_paths:
        has_vp, walls, xref = quick_scan(p)
        file_meta.append((p, has_vp, walls, xref))

    if not file_meta:
        return None, None, "Could not read any files."

    # Separate floor plan files from RCP files
    rcp_paths = [p for p,vp,w,_ in file_meta if vp and is_rcp(p.name)]
    floor_candidates = [(p,vp,w) for p,vp,w,_ in file_meta
                        if vp and not is_rcp(p.name)]
    if not floor_candidates:
        floor_candidates = [(p,vp,w) for p,vp,w,_ in file_meta]

    sheet_path = max(floor_candidates, key=lambda x: sheet_score(x[0].name))[0]
    st.write(f"Floor plan: {sheet_path.name}")
    if rcp_paths:
        st.write(f"RCP detected: {', '.join(p.name for p in rcp_paths)}")

    # Load sheet
    doc_a1 = ezdxf.readfile(str(sheet_path))

    # Find xref
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

    def a1_to_master(ax,ay):
        dx=ax-xref_ix; dy=ay-xref_iy
        cr=math.cos(-xref_rot); sr=math.sin(-xref_rot)
        ux=dx*cr-dy*sr; uy=dx*sr+dy*cr
        return ux/xref_sx, uy/xref_sy

    if xref_name:
        master_path=next((p for p in dxf_paths if p.stem.lower()==xref_name.lower()),None)
        doc_m=ezdxf.readfile(str(master_path)) if master_path else doc_a1
    else:
        doc_m=doc_a1

    msp_m=doc_m.modelspace()

    # Get viewport zones for floor plan
    zones=get_viewport_zones(doc_a1,xref_name,xref_ix,xref_iy,xref_sx,xref_sy,xref_rot)

    # Wall cluster for xref
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
                y1,y2=min(best)-300,max(best)+300
                zones.append((lot_x1,lot_x2,y1,y2))

    if not zones:
        zones=[(-1e9,1e9,-1e9,1e9)]

    # Floor plan center X for RCP mirroring
    floor_cx = (min(z[0] for z in zones) + max(z[1] for z in zones)) / 2

    # Bounds for xref clipping
    all_x1=min(z[0] for z in zones)-200; all_x2=max(z[1] for z in zones)+200
    all_y1=min(z[2] for z in zones)-200; all_y2=max(z[3] for z in zones)+200

    out=ezdxf.new("R2018")
    out_msp=out.modelspace()
    ec=[0]

    # Extract floor plan geometry
    copied=extract_geometry(
        msp_m, zones, doc_m.blocks, out_msp,
        layer_color=8, ec=ec, max_ec=40000,
        mirror_cx=None, xref_name=xref_name,
        all_x1=all_x1 if xref_name else None,
        all_x2=all_x2 if xref_name else None,
        all_y1=all_y1 if xref_name else None,
        all_y2=all_y2 if xref_name else None
    )

    # Extract RCP geometry (mirrored, different color)
    rcp_count = 0
    for rcp_path in rcp_paths:
        try:
            doc_rcp = ezdxf.readfile(str(rcp_path))
            rcp_zones = get_viewport_zones(doc_rcp)
            if not rcp_zones:
                rcp_zones = zones  # fallback to floor plan zones
            rcp_count += extract_geometry(
                doc_rcp.modelspace(), rcp_zones, doc_rcp.blocks, out_msp,
                layer_color=9,  # slightly different gray for RCP
                ec=[0], max_ec=30000,
                mirror_cx=floor_cx  # mirror around floor plan center
            )
            del doc_rcp
            st.write(f"✅ RCP extracted and mirrored: {rcp_path.name}")
        except Exception as ex:
            st.write(f"RCP error: {ex}")

    # Labels
    placed_labels=0
    if not xref_name:
        for e in doc_a1.modelspace():
            try:
                if e.dxftype() in ["TEXT","MTEXT"]:
                    if e.dxftype()=="TEXT":
                        txt=e.dxf.text.strip(); ix,iy=e.dxf.insert.x,e.dxf.insert.y
                        txt_rot=getattr(e.dxf,'rotation',0.0); h=e.dxf.height
                    else:
                        txt=clean_mtext(e.text); ix,iy=e.dxf.insert.x,e.dxf.insert.y
                        txt_rot=getattr(e.dxf,'rotation',0.0)
                        h=getattr(e.dxf,'char_height',20)
                    if len(txt)<2: continue
                    for (X1,X2,Y1,Y2) in zones:
                        if X1<=ix<=X2 and Y1<=iy<=Y2:
                            out_msp.add_text(txt[:50],dxfattribs={
                                "layer":"ROOM-LABELS","color":253,
                                "insert":(ix,iy),"height":h,"rotation":txt_rot})
                            placed_labels+=1
                            break
            except: pass
    else:
        ground_zone=max(zones,key=lambda z:(z[2]+z[3])/2)
        gx1,gx2,gy1,gy2=ground_zone
        zone_x_center=(gx1+gx2)/2
        tl=[]
        for e in doc_a1.modelspace():
            try:
                if e.dxftype() in ["TEXT","MTEXT"]:
                    if e.dxftype()=="TEXT":
                        txt=e.dxf.text.strip(); ix,iy=e.dxf.insert.x,e.dxf.insert.y
                        txt_rot=getattr(e.dxf,'rotation',0.0); h=e.dxf.height
                    else:
                        txt=clean_mtext(e.text); ix,iy=e.dxf.insert.x,e.dxf.insert.y
                        txt_rot=getattr(e.dxf,'rotation',0.0)
                        h=getattr(e.dxf,'char_height',20)
                    if len(txt)<2: continue
                    mx,my=a1_to_master(ix,iy)
                    txt_rot=txt_rot-math.degrees(xref_rot)
                    if gy1<=my<=gy2:
                        tl.append((mx,my,txt,h*xref_sx,txt_rot))
            except: pass
        xc=0
        if tl:
            avg_x=sum(mx for mx,my,t,h,r in tl)/len(tl)
            xc=zone_x_center-avg_x
        for mx,my,txt,h,txt_rot in tl:
            out_msp.add_text(txt[:50],dxfattribs={
                "layer":"ROOM-LABELS","color":253,
                "insert":(mx+xc,my),"height":h,"rotation":txt_rot})
            placed_labels+=1

    out_path=tmp/"floor_plan_clean.dxf"
    out.saveas(str(out_path))
    msg=f"Done. {ec[0]} floor plan entities"
    if rcp_count: msg+=f", {rcp_count} RCP entities (mirrored)"
    msg+=f", {placed_labels} labels."
    return out_path.read_bytes(),msg,None

# CHAT UI
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
    with st.chat_message("assistant"):
        st.download_button("Download floor_plan_clean.dxf",
            data=st.session_state.result,file_name="floor_plan_clean.dxf",
            mime="application/octet-stream")

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
