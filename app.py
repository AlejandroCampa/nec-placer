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
    if any(k in n for k in ["floor plan","ground","a101","a-1"]): return 2
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
                     if e.dxftype()=="INSERT" and e.dxf.name not in SKIP_BLOCKS), None)
        del doc
        return has_vp, walls, xref
    except:
        return False, 0, None

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
        has_vp, walls, xref = quick_scan(p)
        file_meta.append((p, has_vp, walls, xref))
    gc.collect()

    if not file_meta:
        return None, None, "Could not read any files."

    candidates = [(p,vp,w) for p,vp,w,_ in file_meta if vp]
    if not candidates:
        candidates = [(p,vp,w) for p,vp,w,_ in file_meta]
    sheet_path = max(candidates, key=lambda x: sheet_score(x[0].name))[0]

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
        doc_m=doc_a1

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

    out=ezdxf.new("R2018")
    out_msp=out.modelspace()
    ec=[0]

    if xref_name:
        def ib(cx,cy): return all_x1<=cx<=all_x2 and all_y1<=cy<=all_y2
        def explode(bn,ix,iy,sx,sy,rot,d=0):
            if d>5 or ec[0]>80000: return
            if bn not in doc_m.blocks: return
            cr,sr=math.cos(rot),math.sin(rot)
            def xf(px,py):
                lx,ly=px*sx,py*sy
                return ix+lx*cr-ly*sr,iy+lx*sr+ly*cr
            for be in doc_m.blocks[bn]:
                if ec[0]>80000: break
                try:
                    bl=getattr(be.dxf,'layer','0')
                    if bl in SKIP: continue
                    bt=be.dxftype()
                    if bt=="LINE":
                        p1=xf(be.dxf.start.x,be.dxf.start.y)
                        p2=xf(be.dxf.end.x,be.dxf.end.y)
                        if ib((p1[0]+p2[0])/2,(p1[1]+p2[1])/2):
                            out_msp.add_line(p1,p2,dxfattribs={"layer":bl,"color":8})
                            ec[0]+=1
                    elif bt=="LWPOLYLINE":
                        pts=list(be.get_points())
                        if pts:
                            tp=[xf(p[0],p[1]) for p in pts]
                            if ib(sum(p[0] for p in tp)/len(tp),sum(p[1] for p in tp)/len(tp)):
                                out_msp.add_lwpolyline(tp,dxfattribs={"layer":bl,"color":8,"closed":be.is_closed})
                                ec[0]+=1
                    elif bt=="ARC":
                        nc=xf(be.dxf.center.x,be.dxf.center.y)
                        if ib(nc[0],nc[1]):
                            out_msp.add_arc(center=nc,radius=be.dxf.radius*sx,
                                start_angle=be.dxf.start_angle+math.degrees(rot),
                                end_angle=be.dxf.end_angle+math.degrees(rot),
                                dxfattribs={"layer":bl,"color":8})
                            ec[0]+=1
                    elif bt=="CIRCLE":
                        nc=xf(be.dxf.center.x,be.dxf.center.y)
                        if ib(nc[0],nc[1]):
                            out_msp.add_circle(center=nc,radius=be.dxf.radius*sx,
                                dxfattribs={"layer":bl,"color":8})
                            ec[0]+=1
                    elif bt=="SPLINE":
                        sp=list(be.control_points)
                        if sp:
                            tp=[xf(p[0],p[1]) for p in sp]
                            if ib(sum(p[0] for p in tp)/len(tp),sum(p[1] for p in tp)/len(tp)):
                                out_msp.add_lwpolyline(tp,dxfattribs={"layer":bl,"color":8})
                                ec[0]+=1
                    elif bt=="INSERT":
                        ni,nj=xf(be.dxf.insert.x,be.dxf.insert.y)
                        if ib(ni,nj):
                            explode(be.dxf.name,ni,nj,
                                sx*getattr(be.dxf,'xscale',1.0),
                                sy*getattr(be.dxf,'yscale',1.0),
                                rot+math.radians(getattr(be.dxf,'rotation',0.0)),d+1)
                except: pass
    else:
        def explode(bn,ix,iy,sx,sy,rot,d=0):
            if d>5: return
            if bn not in doc_m.blocks: return
            cr,sr=math.cos(rot),math.sin(rot)
            def xf(px,py):
                lx,ly=px*sx,py*sy
                return ix+lx*cr-ly*sr,iy+lx*sr+ly*cr
            for be in doc_m.blocks[bn]:
                try:
                    bl=getattr(be.dxf,'layer','0')
                    if bl in SKIP: continue
                    bt=be.dxftype()
                    if bt=="LINE":
                        out_msp.add_line(xf(be.dxf.start.x,be.dxf.start.y),
                            xf(be.dxf.end.x,be.dxf.end.y),
                            dxfattribs={"layer":bl,"color":8})
                        ec[0]+=1
                    elif bt=="LWPOLYLINE":
                        pts=list(be.get_points())
                        if pts:
                            out_msp.add_lwpolyline([xf(p[0],p[1]) for p in pts],
                                dxfattribs={"layer":bl,"color":8,"closed":be.is_closed})
                            ec[0]+=1
                    elif bt=="ARC":
                        nc=xf(be.dxf.center.x,be.dxf.center.y)
                        out_msp.add_arc(center=nc,radius=be.dxf.radius*sx,
                            start_angle=be.dxf.start_angle+math.degrees(rot),
                            end_angle=be.dxf.end_angle+math.degrees(rot),
                            dxfattribs={"layer":bl,"color":8})
                        ec[0]+=1
                    elif bt=="CIRCLE":
                        nc=xf(be.dxf.center.x,be.dxf.center.y)
                        out_msp.add_circle(center=nc,radius=be.dxf.radius*sx,
                            dxfattribs={"layer":bl,"color":8})
                        ec[0]+=1
                    elif bt=="SPLINE":
                        sp=list(be.control_points)
                        if sp:
                            out_msp.add_lwpolyline([xf(p[0],p[1]) for p in sp],
                                dxfattribs={"layer":bl,"color":8})
                            ec[0]+=1
                    elif bt=="INSERT":
                        ni,nj=xf(be.dxf.insert.x,be.dxf.insert.y)
                        explode(be.dxf.name,ni,nj,
                            sx*getattr(be.dxf,'xscale',1.0),
                            sy*getattr(be.dxf,'yscale',1.0),
                            rot+math.radians(getattr(be.dxf,'rotation',0.0)),d+1)
                except: pass

    copied=0
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
                    if len(txt)<2: continue
                    mx,my=a1_to_master(ix,iy)
                    txt_rot=txt_rot-math.degrees(xref_rot)
                    if my<1000:
                        tl.append((mx,my,txt,h*xref_sx,txt_rot))
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
        # Compute xc from xref transform geometry, not from noisy avg_x
        # a1_to_master(0,0) gives us where A-1 origin sits in Master coords
        # shift that to zone_x_center to align labels
        origin_mx=a1_to_master(0,0)[0]
        xc=zone_x_center-origin_mx
        zone_y1=min(z[2] for z in zones)-500
        zone_y2=max(z[3] for z in zones)+500
        for mx,my,txt,h,txt_rot in tl:
            if zone_y1<=my<=zone_y2:
                out_msp.add_text(txt[:50],dxfattribs={
                    "layer":"ROOM-LABELS","color":253,
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
    def is_ceiling_layer(n): return n not in RCP_SKIP_LAYERS
    floor_cx=(min(z[0] for z in zones)+max(z[1] for z in zones))/2
    rcp_count=0

    for rcp_path in rcp_paths:
        try:
            doc_rcp=ezdxf.readfile(str(rcp_path))
            msp_rcp=doc_rcp.modelspace()
            rcp_zones=[]
            for layout in doc_rcp.layouts:
                if layout.name=="Model": continue
                for e in layout:
                    try:
                        if e.dxftype()=="VIEWPORT":
                            vcp=getattr(e.dxf,'view_center_point',None)
                            vh=getattr(e.dxf,'view_height',None)
                            ps_w=getattr(e.dxf,'width',None)
                            ps_h=getattr(e.dxf,'height',None)
                            if vcp and vh and vh>0:
                                mx,my=vcp.x,vcp.y
                                half_h=vh/2
                                aspect=(ps_w/ps_h) if (ps_w and ps_h and ps_h>0) else 1.5
                                half_w=half_h*aspect
                                if half_w<50 or half_h<50: continue
                                rcp_zones.append((mx-half_w,mx+half_w,my-half_h,my+half_h))
                    except: pass
            if not rcp_zones:
                del doc_rcp; continue
            rcp_ec=[0]
            def explode_rcp(bn,ix,iy,sx,sy,rot,d=0,in_ceil=False):
                if d>3 or rcp_ec[0]>30000: return
                if bn not in doc_rcp.blocks: return
                cr,sr=math.cos(rot),math.sin(rot)
                def xf(px,py):
                    lx,ly=px*sx,py*sy
                    rx=ix+lx*cr-ly*sr; ry=iy+lx*sr+ly*cr
                    return 2*floor_cx-rx,ry
                for be in doc_rcp.blocks[bn]:
                    if rcp_ec[0]>30000: break
                    try:
                        bl=getattr(be.dxf,'layer','0')
                        if bl in SKIP: continue
                        bt=be.dxftype()
                        if bt=="INSERT":
                            ni,nj=xf(be.dxf.insert.x,be.dxf.insert.y)
                            new_in=in_ceil or is_ceiling_layer(bl)
                            explode_rcp(be.dxf.name,ni,nj,
                                sx*getattr(be.dxf,'xscale',1.0),
                                sy*getattr(be.dxf,'yscale',1.0),
                                rot+math.radians(getattr(be.dxf,'rotation',0.0)),
                                d+1,new_in)
                            continue
                        if not in_ceil and not is_ceiling_layer(bl): continue
                        if bt=="LINE":
                            out_msp.add_line(xf(be.dxf.start.x,be.dxf.start.y),
                                xf(be.dxf.end.x,be.dxf.end.y),
                                dxfattribs={"layer":"A-RCP","color":9})
                            rcp_ec[0]+=1
                        elif bt=="LWPOLYLINE":
                            pts=list(be.get_points())
                            if pts:
                                out_msp.add_lwpolyline([xf(p[0],p[1]) for p in pts],
                                    dxfattribs={"layer":"A-RCP","color":9,"closed":be.is_closed})
                                rcp_ec[0]+=1
                        elif bt=="ARC":
                            nc=xf(be.dxf.center.x,be.dxf.center.y)
                            sa=(180-be.dxf.end_angle)%360; ea=(180-be.dxf.start_angle)%360
                            out_msp.add_arc(center=nc,radius=be.dxf.radius*sx,
                                start_angle=sa,end_angle=ea,
                                dxfattribs={"layer":"A-RCP","color":9})
                            rcp_ec[0]+=1
                        elif bt=="CIRCLE":
                            out_msp.add_circle(center=xf(be.dxf.center.x,be.dxf.center.y),
                                radius=be.dxf.radius*sx,dxfattribs={"layer":"A-RCP","color":9})
                            rcp_ec[0]+=1
                    except: pass
            for e in msp_rcp:
                try:
                    layer=getattr(e.dxf,'layer','0')
                    if layer in SKIP: continue
                    t=e.dxftype()
                    if t!="INSERT" and not is_ceiling_layer(layer): continue
                    def mirx(x): return 2*floor_cx-x
                    for (X1,X2,Y1,Y2) in rcp_zones:
                        placed=False
                        if t=="LINE":
                            cx=(e.dxf.start.x+e.dxf.end.x)/2
                            cy=(e.dxf.start.y+e.dxf.end.y)/2
                            if X1<=cx<=X2 and Y1<=cy<=Y2:
                                out_msp.add_line((mirx(e.dxf.start.x),e.dxf.start.y),
                                    (mirx(e.dxf.end.x),e.dxf.end.y),
                                    dxfattribs={"layer":"A-RCP","color":9})
                                placed=True
                        elif t=="LWPOLYLINE":
                            pts=list(e.get_points())
                            if pts:
                                cx=sum(p[0] for p in pts)/len(pts)
                                cy=sum(p[1] for p in pts)/len(pts)
                                if X1<=cx<=X2 and Y1<=cy<=Y2:
                                    out_msp.add_lwpolyline([(mirx(p[0]),p[1]) for p in pts],
                                        dxfattribs={"layer":"A-RCP","color":9,"closed":e.is_closed})
                                    placed=True
                        elif t=="ARC":
                            cx,cy=e.dxf.center.x,e.dxf.center.y
                            if X1<=cx<=X2 and Y1<=cy<=Y2:
                                sa=(180-e.dxf.end_angle)%360; ea=(180-e.dxf.start_angle)%360
                                out_msp.add_arc(center=(mirx(cx),cy),radius=e.dxf.radius,
                                    start_angle=sa,end_angle=ea,
                                    dxfattribs={"layer":"A-RCP","color":9})
                                placed=True
                        elif t=="CIRCLE":
                            cx,cy=e.dxf.center.x,e.dxf.center.y
                            if X1<=cx<=X2 and Y1<=cy<=Y2:
                                out_msp.add_circle(center=(mirx(cx),cy),radius=e.dxf.radius,
                                    dxfattribs={"layer":"A-RCP","color":9})
                                placed=True
                        elif t=="INSERT":
                            cx,cy=e.dxf.insert.x,e.dxf.insert.y
                            if X1<=cx<=X2 and Y1<=cy<=Y2:
                                explode_rcp(e.dxf.name,mirx(cx),cy,
                                    getattr(e.dxf,'xscale',1.0),
                                    getattr(e.dxf,'yscale',1.0),
                                    math.radians(getattr(e.dxf,'rotation',0.0)),
                                    0,is_ceiling_layer(layer))
                                placed=True
                        if placed:
                            rcp_count+=1; break
                except: pass
            del doc_rcp
            gc.collect()
        except Exception as ex:
            st.write(f"RCP error: {ex}")

    out_path=tmp/"floor_plan_clean.dxf"
    out.saveas(str(out_path))
    msg=f"Done. {ec[0]} entities, {placed_labels} labels"
    if debug_info: msg+=" | "+" | ".join(debug_info)
    if rcp_paths: msg+=f", RCP on layer A-RCP"
    return out_path.read_bytes(),msg+".",None

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
