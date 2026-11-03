"""Interactive perspective 3D arm drawn as shaded meshes on a Tk canvas."""
import math
from functools import lru_cache
import tkinter as tk
import numpy as np
from PIL import Image, ImageTk
from rasterizer import rasterize
from tooling import tool_parts, tool_placement
from robot_assets import load_robot_mesh, model_family
from kinematics import MODELS, VIEW_FRAMES, forward_kinematics, mounting_matrix, pose_matrix, view_base_transform
import ui_theme as theme

DISPLAY_OPTIONS = {
    "grid": "Ground grid",
    "axes": "Coordinate axes",
    "live_tcp": "Live TCP",
    "saved_tcp": "Installation TCP",
    "tool_tcp": "Preview tool TCP",
    "payload": "Payload / center of gravity",
    "labels": "Robot labels",
    "info": "Viewport information",
}

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
        super().__init__(parent, background=theme.PANEL, highlightthickness=0, width=700, height=560)
        self.model = model
        self.robot_links = load_robot_mesh(model)
        self.tool = "None"
        self.tool_tcp_source = "Tool preset"
        self.gripper_opening = .5
        self.visibility = {key: key == "grid" for key in DISPLAY_OPTIONS}
        self.view_frame = VIEW_FRAMES[0]
        self.tcp_view_reference = None
        self.sample = None
        self.stream_state = "Live joint geometry"
        self.installation = None
        self.yaw, self.elevation, self.zoom = -.8, .45, 1.35
        self.camera_anchor = None
        self.camera_distance = None
        self.pan_offset = np.zeros(3)
        self.drag = None
        self.pan_drag = None
        self.mesh_image = None
        self.mesh_item = self.create_image(0, 0, anchor="nw", tags="mesh")
        self.rendered_triangles = 0
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.orbit)
        self.bind("<ButtonRelease-1>", self.end_drag)
        self.bind("<ButtonPress-3>", self.start_pan)
        self.bind("<B3-Motion>", self.pan)
        self.bind("<ButtonRelease-3>", self.end_drag)
        self.bind("<MouseWheel>", lambda e: self.zoom_by(1.1 if e.delta > 0 else 1/1.1))
        self.bind("<Button-4>", lambda _: self.zoom_by(1.1))
        self.bind("<Button-5>", lambda _: self.zoom_by(1/1.1))
        theme.register_theme_callback(self, self.refresh_theme)

    def refresh_theme(self):
        self.configure(background=theme.PANEL)
        self.redraw()

    def set_view_frame(self, frame):
        if frame not in VIEW_FRAMES:
            raise ValueError(f"Unknown view frame: {frame}")
        self.view_frame = frame
        self.tcp_view_reference = (tuple(self.sample.tcp_pose)
                                   if frame == "Live TCP" and self.sample is not None else None)
        self.camera_anchor = None
        self.pan_offset[:] = 0
        self.redraw()

    def set_stream_state(self, state):
        if self.stream_state != state:
            self.stream_state = state
            self.redraw()

    def start_drag(self, event):
        self.pan_drag = None
        self.drag = (event.x, event.y)

    def start_pan(self, event):
        self.drag = None
        self.pan_drag = (event.x, event.y)
        self.configure(cursor='fleur')

    def end_drag(self, _event=None):
        self.drag = self.pan_drag = None
        self.configure(cursor='')

    def pan(self, event):
        if self.pan_drag is None:
            return
        dx, dy = event.x - self.pan_drag[0], event.y - self.pan_drag[1]
        # Translate the camera target in its screen plane, at target depth.
        # Scaling by focal length keeps dragging natural at every zoom level.
        self.pan_offset += (-dx*self.right + dy*self.up) * self.distance/self.focal
        self.pan_drag = (event.x, event.y)
        self.redraw()

    def orbit(self, event):
        if self.drag is None:
            return
        self.yaw -= (event.x - self.drag[0]) * .008
        self.elevation = float(np.clip(self.elevation + (event.y - self.drag[1]) * .008, -1.45, 1.45))
        self.drag = (event.x, event.y)
        self.redraw()

    def zoom_by(self, factor):
        self.zoom = float(np.clip(self.zoom * factor, .4, 3.))
        self.redraw()

    def reset_camera(self):
        self.yaw, self.elevation, self.zoom = -.8, .45, 1.35
        self.camera_anchor = None
        self.pan_offset[:] = 0
        self.end_drag()
        self.redraw()

    def update_robot(self, sample, installation, render=True):
        changed = (self.sample is None or self.sample.joints != sample.joints
                   or self.sample.tcp_pose != sample.tcp_pose or self.installation is not installation)
        old_mount = self.installation.mounting if self.installation else None
        new_mount = installation.mounting if installation else None
        if self.view_frame == 'World (mounting)' and old_mount != new_mount:
            self.camera_anchor = None
            self.pan_offset[:] = 0
        self.sample, self.installation = sample, installation
        if self.view_frame == "Live TCP" and self.tcp_view_reference is None:
            self.tcp_view_reference = tuple(sample.tcp_pose)
        if changed and render:
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
        if not self.visibility["labels"]:
            return
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

    def shaded_faces(self, vertices, normals, color, shading=None):
        camera = self.center + self.eye_direction*self.distance
        to_eye = camera - vertices.mean(axis=1)
        visible = np.sum(normals*to_eye, axis=1) > 0
        if not visible.any():
            return
        rgb = (np.array(tuple(int(color[i:i+2], 16) for i in (1, 3, 5)))
               if isinstance(color, str) else np.asarray(color))
        rgb = np.broadcast_to(rgb, (len(vertices), 3))[visible]
        lighting_normals = (np.repeat(normals[:, None, :], vertices.shape[1], axis=1)
                            if shading is None else shading)[visible]
        vertices = vertices[visible]
        to_eye = camera - vertices
        to_eye /= np.maximum(np.linalg.norm(to_eye, axis=2)[:, :, None], 1e-9)
        diffuse = np.maximum(0, lighting_normals @ self.light)
        fill = np.maximum(0, lighting_normals @ (-self.right*.8 + self.up*.6))
        half = to_eye + self.light
        half /= np.maximum(np.linalg.norm(half, axis=2)[:, :, None], 1e-9)
        highlight = np.maximum(0, np.sum(lighting_normals*half, axis=2))
        metal = ((np.ptp(rgb, axis=1) < 55) & (rgb.mean(axis=1) > 115))[:, None]
        reflection = np.where(metal, 32*highlight**18 + 24*highlight**90, 15*highlight**38)
        colors = np.clip(rgb[:, None, :]*(.45 + .55*diffuse + .12*fill)[:, :, None]
                         + reflection[:, :, None], 0, 255)
        xy, depths = self.project(vertices.reshape(-1, 3))
        xy = xy.reshape(len(vertices), vertices.shape[1], 2)
        depths = depths.reshape(len(vertices), vertices.shape[1])
        # Robot meshes are triangles; convex preview-tool faces use triangle fans.
        indices = np.array([[0, i, i+1] for i in range(1, vertices.shape[1]-1)])
        self.faces.append((xy[:, indices].reshape(-1, 3, 2),
                           depths[:, indices].reshape(-1, 3),
                           colors[:, indices].reshape(-1, 3, 3)))

    def draw_mesh(self):
        if not self.faces:
            self.itemconfigure(self.mesh_item, state="hidden")
            self.rendered_triangles = 0
            return
        points, depths, colors = (np.concatenate([batch[i] for batch in self.faces]) for i in range(3))
        self.rendered_triangles = len(points)
        # Rasterize only the robot's screen bounds, not the empty viewport.
        # This makes supersampling affordable even in a large desktop window.
        left = max(0, int(np.floor(points[:, :, 0].min()))-2)
        top = max(0, int(np.floor(points[:, :, 1].min()))-2)
        right = min(self.w, int(np.ceil(points[:, :, 0].max()))+2)
        bottom = min(self.h, int(np.ceil(points[:, :, 1].max()))+2)
        if right <= left or bottom <= top:
            self.itemconfigure(self.mesh_item, state="hidden")
            return
        crop_width, crop_height = right-left, bottom-top
        scale = min(2., 2400 / max(crop_width, crop_height))
        width, height = max(1, int(crop_width*scale)), max(1, int(crop_height*scale))
        rgba = rasterize((points-np.array([left, top]))*scale, depths, colors, width, height)
        picture = Image.fromarray(rgba).resize((crop_width, crop_height), Image.Resampling.LANCZOS)
        self.mesh_image = ImageTk.PhotoImage(picture, master=self)
        self.coords(self.mesh_item, left, top)
        self.itemconfigure(self.mesh_item, image=self.mesh_image, state="normal")
        self.tag_raise("mesh")

    def robot_mesh(self, frames):
        """Rigidly pose the selected model's official link geometry."""
        for frame, (vertices, normals, colors, shading) in zip(frames, self.robot_links):
            rotation, origin = frame[:3, :3], frame[:3, 3]
            self.shaded_faces(vertices @ rotation.T + origin, normals @ rotation.T, colors, shading @ rotation.T)

    def tool_mesh(self, flange):
        rotation, origin = flange[:3, :3], flange[:3, 3]
        for kind, *part in tool_parts(self.tool, self.gripper_opening):
            if kind == "cylinder":
                start, end, radius, end_radius, color = part
                self.cylinder(rotation @ start + origin, rotation @ end + origin,
                              radius, color, end_radius=end_radius)
            else:
                center, dimensions, color = part
                center, half = np.array(center), np.array(dimensions)/2
                for axis in range(3):
                    u, v = (axis+1)%3, (axis+2)%3
                    for sign in (-1, 1):
                        normal = np.eye(3)[axis]*sign
                        corners = np.tile(center+normal*half[axis], (4, 1))
                        corners[:, u] += np.array([-1, 1, 1, -1])*half[u]
                        corners[:, v] += np.array([-1, -1, 1, 1])*half[v]
                        self.shaded_faces((corners @ rotation.T+origin)[None, :, :],
                                          (rotation @ normal)[None, :], color)

    def frame_axes(self, transform, size=.1, labels=False):
        if not self.visibility["axes"]:
            return
        origin = transform[:3, 3]
        colors = ("#b93543", "#32783c", "#2867ad") if theme.MODE == 'light' else ("#f07178", "#8cce8a", "#7eb8ff")
        for i, color in enumerate(colors):
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
        frames = None
        data = self.installation
        if self.sample is not None:
            mount = mounting_matrix(*data.mounting) if data and data.mounting is not None else np.eye(4)
            reference = self.tcp_view_reference if self.view_frame == "Live TCP" else self.sample.tcp_pose
            mount = view_base_transform(self.view_frame, mount, reference)
            frames = forward_kinematics(self.model, self.sample.joints, mount)
            if self.camera_anchor is None:
                # Fit actual link geometry once, in the selected display frame.
                # Keep that target stable as live samples/playback advance.
                vertices = np.concatenate([link[0].reshape(-1, 3) @ frame[:3, :3].T + frame[:3, 3]
                                           for frame, link in zip(frames, self.robot_links)])
                basis = np.array([self.right, self.up, self.eye_direction])
                camera_points = vertices @ basis.T
                self.camera_anchor = ((camera_points.min(axis=0) + camera_points.max(axis=0))*.5) @ basis
                radius = np.linalg.norm(vertices - self.camera_anchor, axis=1).max()
                # A bounding sphere leaves 10% margin at the default zoom and
                # remains fitted when the viewport is resized or orbited.
                self.camera_distance = max(2.7*reach, radius*math.sqrt(1+(1.7*1.35/.4)**2))
            self.center = self.camera_anchor + self.pan_offset
            self.distance = self.camera_distance
        else:
            self.center += self.pan_offset
        self.light = self.eye_direction*.35 - self.right*.45 + self.up*.82
        self.light /= np.linalg.norm(self.light)
        if self.visibility["grid"]:
            for step in np.linspace(-reach, reach, 13):
                self.line3([(step, -reach, 0), (step, reach, 0)], theme.BORDER)
                self.line3([(-reach, step, 0), (reach, step, 0)], theme.BORDER)
        self.frame_axes(np.eye(4), .22*reach, labels=True)
        if self.sample is None:
            self.itemconfigure("mesh", state="hidden")
            self.create_text(self.w/2, self.h/2, text="Waiting for live joint positions…",
                             fill=theme.INK, font=("TkDefaultFont", 13), tags="scene")
            return
        points = np.array([f[:3, 3] for f in frames])
        self.faces = []
        size = min(.075, reach * .045)
        self.robot_mesh(frames)
        live_tcp = mount @ pose_matrix(self.sample.tcp_pose)
        tool_mount, preview_tcp, tcp_source = tool_placement(
            self.tool, frames[-1], self.tool_tcp_source,
            data.tcp if data else None, live_tcp)
        if self.tool != "None":
            # A generic adapter shows any offset introduced by TCP alignment.
            self.cylinder(points[-1], tool_mount[:3, 3], size*.3, "#667988")
            self.tool_mesh(tool_mount)
        self.draw_mesh()
        if self.tool != "None" and self.visibility["tool_tcp"]:
            color = '#8a5700' if theme.MODE == 'light' else '#ffcf80'
            self.frame_axes(preview_tcp, .055*reach)
            xy, _ = self.project([preview_tcp[:3, 3]])
            x, y = xy[0]
            self.create_oval(x-4, y-4, x+4, y+4, outline=color, width=2, tags="scene")
            self.text3(preview_tcp[:3, 3], f"  Tool TCP · {tcp_source}", color)
        if self.visibility["live_tcp"]:
            color = '#16765d' if theme.MODE == 'light' else '#63e2bb'
            self.line3([points[-1], live_tcp[:3, 3]], color, 3)
            self.frame_axes(live_tcp, .08*reach)
            self.text3(live_tcp[:3, 3], "  Live TCP", color)
        if self.visibility["saved_tcp"] and data and data.tcp is not None:
            color = '#7751b5' if theme.MODE == 'light' else '#c5a0ff'
            saved_tcp = frames[-1] @ pose_matrix(data.tcp)
            self.line3([points[-1], saved_tcp[:3, 3]], color, 2, (4, 3))
            self.text3(saved_tcp[:3, 3], "  Installation TCP", color)
        if self.visibility["payload"] and data and data.payload_mass is not None and data.payload_cog is not None and data.payload_mass > 0:
            color = '#8a5700' if theme.MODE == 'light' else '#ffc574'
            cog = (frames[-1] @ np.array((*data.payload_cog, 1)))[:3]
            self.line3([points[-1], cog], color, 2, (2, 3))
            xy, _ = self.project([cog])
            x, y = xy[0]
            self.create_oval(x-5, y-5, x+5, y+5, fill=color, outline="", tags="scene")
            self.text3(cog, f"  CoG · {data.payload_mass:g} kg", color)
        if not self.visibility["info"]:
            return
        self.create_text(18, 18, text=f"{self.model} · {model_family(self.model)}   •   {self.stream_state}   •   {self.view_frame}",
                         fill=theme.INK, anchor="nw", font=("TkDefaultFont", 11, "bold"), tags="scene")
        self.create_text(18, self.h-18, text="Left-drag to rotate  ·  Right-drag to pan  ·  Scroll to zoom",
                         fill=theme.MUTED, anchor="sw", tags="scene")
