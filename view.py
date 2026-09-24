"""Interactive perspective 3D arm drawn as shaded meshes on a Tk canvas."""
import math
from functools import lru_cache
import tkinter as tk
import numpy as np
from kinematics import MODELS, VIEW_FRAMES, forward_kinematics, mounting_matrix, pose_matrix, view_base_transform


@lru_cache(maxsize=256)
def cylinder_geometry(length, radius, end_radius, bevel, segments=40):
    """Cache local mesh topology; joint motion only changes its rigid transform."""
    angles = np.arange(segments) * (2*math.pi/segments)
    radial = np.column_stack((np.cos(angles), np.sin(angles), np.zeros(segments)))
    edge = min(radius*.16, length*.15)
    stations = ([(0, radius*.87), (edge, radius),
                 (length-edge, end_radius), (length, end_radius*.87)] if bevel
                else [(0, radius), (length, end_radius)])
    rings = np.array([np.array([0, 0, distance]) + radial*r for distance, r in stations])
    vertices = np.concatenate([
        np.stack((a, np.roll(a, -1, axis=0), np.roll(b, -1, axis=0), b), axis=1)
        for a, b in zip(rings[:-1], rings[1:])])
    normals = np.cross(vertices[:, 1]-vertices[:, 0], vertices[:, 3]-vertices[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    return vertices, normals, rings[[0, -1]]

class RobotView(tk.Canvas):
    def __init__(self, parent, model):
        super().__init__(parent, background="#101923", highlightthickness=0, width=700, height=560)
        self.model = model
        self.view_frame = VIEW_FRAMES[0]
        self.tcp_view_reference = None
        self.sample = None
        self.stream_state = "Live joint geometry"
        self.installation = None
        self.yaw, self.elevation, self.zoom = -.8, .45, 1.
        self.drag = None
        self.mesh_items = []
        self.mesh_colors = []
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.orbit)
        self.bind("<MouseWheel>", lambda e: self.zoom_by(1.1 if e.delta > 0 else 1/1.1))
        self.bind("<Button-4>", lambda _: self.zoom_by(1.1))
        self.bind("<Button-5>", lambda _: self.zoom_by(1/1.1))

    def set_view_frame(self, frame):
        if frame not in VIEW_FRAMES:
            raise ValueError(f"Unknown view frame: {frame}")
        self.view_frame = frame
        self.tcp_view_reference = (tuple(self.sample.tcp_pose)
                                   if frame == "Live TCP" and self.sample is not None else None)
        self.redraw()

    def set_stream_state(self, state):
        if self.stream_state != state:
            self.stream_state = state
            self.redraw()

    def start_drag(self, event):
        self.drag = (event.x, event.y)

    def orbit(self, event):
        if self.drag:
            self.yaw -= (event.x - self.drag[0]) * .008
            self.elevation = float(np.clip(self.elevation + (event.y - self.drag[1]) * .008, -1.45, 1.45))
        self.drag = (event.x, event.y)
        self.redraw()

    def zoom_by(self, factor):
        self.zoom = float(np.clip(self.zoom * factor, .4, 3.))
        self.redraw()

    def reset_camera(self):
        self.yaw, self.elevation, self.zoom = -.8, .45, 1.
        self.redraw()

    def update_robot(self, sample, installation):
        self.sample, self.installation = sample, installation
        if self.view_frame == "Live TCP" and self.tcp_view_reference is None:
            self.tcp_view_reference = tuple(sample.tcp_pose)
        self.redraw()

    def project(self, points):
        points = np.asarray(points) - self.center
        depth = self.distance - points @ self.eye_direction
        scale = self.focal / np.maximum(depth, .05)
        xy = np.column_stack((self.w/2 + (points @ self.right)*scale,
                              self.h/2 - (points @ self.up)*scale))
        return xy, depth

    def line3(self, points, color, width=1, dash=None):
        xy, _ = self.project(points)
        self.create_line(*xy.ravel(), fill=color, width=width, dash=dash, tags="scene")

    def text3(self, point, text, color):
        xy, _ = self.project([point])
        self.create_text(*xy[0], text=text, fill=color, anchor="sw",
                         font=("TkDefaultFont", 10), tags="scene")

    def cylinder(self, start, end, radius, color, end_radius=None, bevel=True, segments=40):
        """Shaded, bevelled housing with vectorized lighting and face culling."""
        start, end = np.asarray(start), np.asarray(end)
        axis = end - start
        length = np.linalg.norm(axis)
        if length < 1e-8:
            return
        axis /= length
        helper = np.array([0., 0., 1.]) if abs(axis[2]) < .9 else np.array([1., 0., 0.])
        u = np.cross(axis, helper)
        u /= np.linalg.norm(u)
        v = np.cross(axis, u)
        basis = np.column_stack((u, v, axis))
        end_radius = radius if end_radius is None else end_radius
        depth = self.distance - np.dot((start+end)*.5-self.center, self.eye_direction)
        screen_radius = self.focal*max(radius, end_radius)/max(depth, .05)
        # Spend mesh detail where it is visible; zooming in restores full curves.
        detail = 40 if screen_radius > 36 else 32 if screen_radius > 20 else 24 if screen_radius > 10 else 16
        segments = min(segments, detail)
        vertices, normals, caps = cylinder_geometry(round(float(length), 12), radius, end_radius, bevel, segments)
        self.shaded_faces(vertices @ basis.T + start, normals @ basis.T, color)
        self.shaded_faces(caps @ basis.T + start, np.array([-axis, axis]), color)

    def shaded_faces(self, vertices, normals, color):
        centers = vertices.mean(axis=1)
        camera = self.center + self.eye_direction*self.distance
        to_eye = camera - centers
        to_eye /= np.linalg.norm(to_eye, axis=1)[:, None]
        visible = np.sum(normals*to_eye, axis=1) > 0
        vertices, normals, to_eye = vertices[visible], normals[visible], to_eye[visible]
        if not len(vertices):
            return
        rgb = np.array(tuple(int(color[i:i+2], 16) for i in (1, 3, 5)))
        diffuse = np.maximum(0, normals @ self.light)
        fill = np.maximum(0, normals @ (-self.right*.8 + self.up*.6))
        half = to_eye + self.light
        half /= np.maximum(np.linalg.norm(half, axis=1)[:, None], 1e-9)
        highlight = np.maximum(0, np.sum(normals*half, axis=1))
        # Blue polymer caps and rubber seals have softer reflections than aluminum.
        metal = max(rgb)-min(rgb) < 55 and rgb.mean() > 115
        reflection = (52*highlight**18 + 46*highlight**90 if metal
                      else (24 if rgb.mean() > 100 else 10)*highlight**38)
        rim = (1-np.maximum(0, np.sum(normals*to_eye, axis=1)))**3
        colors = np.clip(rgb[None, :]*(.35 + .62*diffuse + .16*fill + .07*rim)[:, None]
                         + reflection[:, None], 0, 255).astype(int)
        colors = (colors // 3) * 3
        xy, depths = self.project(vertices.reshape(-1, 3))
        xy = xy.reshape(len(vertices), -1, 2)
        depths = depths.reshape(len(vertices), -1).mean(axis=1)
        for depth, polygon, rgb in zip(depths, xy, colors):
            shade = "#" + "".join(f"{c:02x}" for c in rgb)
            self.faces.append((float(depth), polygon, shade))

    def socket_head(self, center, normal, tangent, radius):
        """Small machined fastener with a dark hex socket, kept inexpensive to draw."""
        tangent = tangent / np.linalg.norm(tangent)
        other = np.cross(normal, tangent)
        for count, scale, offset, color in ((16, 1., 0., "#aebdc7"),
                                          (6, .48, radius*.015, "#19242c")):
            angles = np.arange(count)*2*math.pi/count
            ring = center + normal*offset + radius*scale*(
                np.cos(angles)[:, None]*tangent + np.sin(angles)[:, None]*other)
            self.shaded_faces(ring[None, :, :], normal[None, :], color)

    def joint_housing(self, center, axis, tangent, radius, length):
        # Aluminum motor body, dark seals, satin-blue cap and recessed fasteners.
        self.cylinder(center-axis*length/2, center+axis*length/2, radius, "#c4cdd3")
        other = np.cross(axis, tangent)
        for direction in (-1, 1):
            normal = axis*direction
            face = center + normal*length/2
            self.cylinder(face-normal*radius*.08, face+normal*radius*.05,
                          radius*.98, "#273b49", bevel=False, segments=32)
            self.cylinder(face+normal*radius*.05, face+normal*radius*.15,
                          radius*.87, "#72b4d7")
            cap = face+normal*radius*.153
            self.cylinder(cap, cap+normal*radius*.012, radius*.2,
                          "#84bedc", bevel=False, segments=20)
            for angle in np.arange(4)*math.pi/2 + math.pi/4:
                screw = cap + radius*.70*(math.cos(angle)*tangent + math.sin(angle)*other)
                self.socket_head(screw, normal, tangent, radius*.047)

    def robot_mesh(self, frames, size):
        points = np.array([frame[:3, 3] for frame in frames])
        base_axis = frames[0][:3, 2]
        # Mounting plate and its socket-head bolts orient with the robot base.
        self.cylinder(points[0]-base_axis*.058, points[0]-base_axis*.044,
                      size*1.95, "#9cabb5")
        for angle in np.arange(6)*math.pi/3:
            bolt = points[0]-base_axis*.043 + size*1.73*(
                math.cos(angle)*frames[0][:3, 0] + math.sin(angle)*frames[0][:3, 1])
            self.socket_head(bolt, base_axis, frames[0][:3, 0], size*.105)
        self.cylinder(points[0]-base_axis*.045, points[0], size*1.65, "#344957")
        self.cylinder(points[0], points[0]+base_axis*.06, size*1.55, "#aabdc9",
                      end_radius=size*1.3)
        for i in range(6):
            start, end = points[i], points[i+1]
            radius = size*(.83 if i < 3 else .62)
            self.cylinder(start, end, radius, "#cbd5dc", end_radius=radius*.88)
            delta = end-start
            length = np.linalg.norm(delta)
            if length > size*3:
                direction = delta/length
                # Collars give the long upper-arm and forearm tubes defined ends.
                for point in (start+direction*size, end-direction*size):
                    self.cylinder(point-direction*size*.12, point+direction*size*.12,
                                  radius*1.07, "#8c9eaa", bevel=False)
            joint_radius = size*(1.16 if i < 3 else .86)
            self.joint_housing(start, frames[i][:3, 2], frames[i][:3, 0], joint_radius, joint_radius*1.6)
        # Tool flange: a dark adapter rim and machined silver mounting face.
        tool_axis = frames[-1][:3, 2]
        flange = points[-1]
        self.cylinder(flange-tool_axis*size*.28, flange, size*.79, "#253844")
        self.cylinder(flange-tool_axis*size*.09, flange+tool_axis*size*.035, size*.74, "#d6dee3")
        self.cylinder(flange+tool_axis*size*.036, flange+tool_axis*size*.038,
                      size*.26, "#23323d", bevel=False, segments=24)
        for angle in np.arange(6)*math.pi/3:
            bolt = flange + size*.52*(math.cos(angle)*frames[-1][:3, 0]
                                      + math.sin(angle)*frames[-1][:3, 1])
            self.cylinder(bolt+tool_axis*size*.035, bolt+tool_axis*size*.05,
                          size*.075, "#34414a", bevel=False, segments=16)

    def frame_axes(self, transform, size=.1, labels=False):
        origin = transform[:3, 3]
        for i, color in enumerate(("#f07178", "#8cce8a", "#7eb8ff")):
            end = origin + size*transform[:3, i]
            self.line3([origin, end], color, 2)
            if labels:
                self.text3(end, " " + "XYZ"[i], color)

    def redraw(self):
        self.delete("scene")
        self.w, self.h = max(self.winfo_width(), 100), max(self.winfo_height(), 100)
        a2, a3, d1, d4, d5, d6 = MODELS[self.model]
        reach = abs(a2) + abs(a3) + d1 + d4 + d5 + d6
        self.center = np.array([0., 0., 0. if self.view_frame == "Live TCP" else .15*reach])
        self.distance, self.focal = 2.7*reach, min(self.w, self.h)*1.7*self.zoom
        self.eye_direction = np.array([math.cos(self.elevation)*math.cos(self.yaw),
                                       math.cos(self.elevation)*math.sin(self.yaw), math.sin(self.elevation)])
        self.right = np.array([-math.sin(self.yaw), math.cos(self.yaw), 0])
        self.up = np.cross(self.eye_direction, self.right)
        self.light = self.eye_direction*.35 - self.right*.45 + self.up*.82
        self.light /= np.linalg.norm(self.light)
        for step in np.linspace(-reach, reach, 13):
            self.line3([(step, -reach, 0), (step, reach, 0)], "#233342")
            self.line3([(-reach, step, 0), (reach, step, 0)], "#233342")
        self.frame_axes(np.eye(4), .22*reach, labels=True)
        if self.sample is None:
            self.itemconfigure("mesh", state="hidden")
            self.create_text(self.w/2, self.h/2, text="Waiting for live joint positions…",
                             fill="#dce7f0", font=("TkDefaultFont", 16), tags="scene")
            return
        data = self.installation
        mount = mounting_matrix(*data.mounting) if data and data.mounting is not None else np.eye(4)
        reference = self.tcp_view_reference if self.view_frame == "Live TCP" else self.sample.tcp_pose
        mount = view_base_transform(self.view_frame, mount, reference)
        frames = forward_kinematics(self.model, self.sample.joints, mount)
        points = np.array([f[:3, 3] for f in frames])
        self.faces = []
        size = min(.075, reach * .045)
        self.robot_mesh(frames, size)
        self.itemconfigure("mesh", state="normal")
        for index, (_, xy, color) in enumerate(sorted(self.faces, key=lambda f: f[0], reverse=True)):
            if index == len(self.mesh_items):
                item = self.create_polygon(*xy.ravel().tolist(), fill=color, outline=color, tags="mesh")
                self.mesh_items.append(item)
                self.mesh_colors.append(color)
            else:
                item = self.mesh_items[index]
                self.coords(item, *xy.ravel().tolist())
                if self.mesh_colors[index] != color:
                    self.itemconfigure(item, fill=color, outline=color)
                    self.mesh_colors[index] = color
        for item in self.mesh_items[len(self.faces):]:
            self.itemconfigure(item, state="hidden")
        self.tag_raise("mesh")
        live_tcp = mount @ pose_matrix(self.sample.tcp_pose)
        self.line3([points[-1], live_tcp[:3, 3]], "#63e2bb", 3)
        self.frame_axes(live_tcp, .08*reach)
        self.text3(live_tcp[:3, 3], "  Live TCP", "#63e2bb")
        if data and data.tcp is not None:
            saved_tcp = frames[-1] @ pose_matrix(data.tcp)
            self.line3([points[-1], saved_tcp[:3, 3]], "#c5a0ff", 2, (4, 3))
            self.text3(saved_tcp[:3, 3], "  Installation TCP", "#c5a0ff")
        if data and data.payload_mass is not None and data.payload_cog is not None and data.payload_mass > 0:
            cog = (frames[-1] @ np.array((*data.payload_cog, 1)))[:3]
            self.line3([points[-1], cog], "#ffc574", 2, (2, 3))
            xy, _ = self.project([cog])
            x, y = xy[0]
            self.create_oval(x-5, y-5, x+5, y+5, fill="#ffc574", outline="", tags="scene")
            self.text3(cog, f"  CoG · {data.payload_mass:g} kg", "#ffc574")
        self.create_text(18, 18, text=f"{self.model}   •   {self.stream_state}   •   {self.view_frame}",
                         fill="#dce7f0", anchor="nw", font=("TkDefaultFont", 12, "bold"), tags="scene")
        self.create_text(18, self.h-18, text="Drag to orbit  ·  Scroll to zoom  ·  Grid and distances in metres",
                         fill="#9baec0", anchor="sw", tags="scene")
