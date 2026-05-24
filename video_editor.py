"""Embedded video editor — trim, preview, export recorded clips."""

import os
import time
from pathlib import Path

import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QSlider, QListWidget, QListWidgetItem, QFileDialog, QSplitter,
    QSplitterHandle, QFrame, QProgressBar, QComboBox, QSpinBox,
    QMessageBox, QSizePolicy
)
from PyQt6.QtCore    import Qt, QThread, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui     import QPixmap, QImage, QIcon, QCursor
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame

try:
    import cv2
    CV2_OK = True
except Exception:
    CV2_OK = False


ACCENT = '#FF2020'
BG     = '#0a0000'
PANEL  = '#130000'
BORDER = 'rgba(255,30,30,0.18)'


class LiveHandle(QSplitterHandle):
    """Drag handle whose mouse events are handled entirely in Python.

    Qt6's built-in opaque-resize path has a macOS coordinate bug that inverts
    the drag direction. By NOT calling super() on any mouse event we fully bypass
    Qt's internal drag-state machine.  We use globalPosition() deltas so neither
    DPI scaling nor widget-local coordinate frames matter.
    """
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setCursor(
            Qt.CursorShape.SplitHCursor
            if orientation == Qt.Orientation.Horizontal
            else Qt.CursorShape.SplitVCursor
        )
        self._press_pos   = None   # QPoint in global screen coords at press
        self._press_sizes = None   # splitter sizes snapshot at press

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            e.accept()
            self._press_pos   = e.globalPosition().toPoint()
            self._press_sizes = list(self.splitter().sizes())
        # intentionally no super() — stops Qt activating its own drag state

    def mouseMoveEvent(self, e):
        if self._press_pos is None:
            return
        e.accept()
        spl   = self.splitter()
        gp    = e.globalPosition().toPoint()
        delta = (gp.x() - self._press_pos.x()
                 if spl.orientation() == Qt.Orientation.Horizontal
                 else gp.y() - self._press_pos.y())

        # Locate which gap we sit in (1-based: handle[i] is between widget[i-1]/widget[i])
        idx = 1
        for i in range(1, spl.count()):
            if spl.handle(i) is self:
                idx = i
                break

        sizes = list(self._press_sizes)
        if idx < len(sizes):
            total      = sizes[idx - 1] + sizes[idx]
            s0         = max(20, min(total - 20, sizes[idx - 1] + delta))
            sizes[idx - 1] = s0
            sizes[idx]     = total - s0
            spl.setSizes(sizes)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            e.accept()
        self._press_pos   = None
        self._press_sizes = None
        # intentionally no super()


class LiveSplitter(QSplitter):
    def createHandle(self):
        return LiveHandle(self.orientation(), self)


class ExportThread(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, src, dst, start_f, end_f, fps, size):
        super().__init__()
        self.src     = src
        self.dst     = dst
        self.start_f = start_f
        self.end_f   = end_f
        self.fps     = fps
        self.size    = size

    def run(self):
        if not CV2_OK:
            self.finished.emit(False, "opencv-python not installed")
            return
        try:
            cap = cv2.VideoCapture(self.src)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            end   = self.end_f if self.end_f > 0 else total

            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out    = cv2.VideoWriter(self.dst, fourcc, self.fps, self.size)

            cap.set(cv2.CAP_PROP_POS_FRAMES, self.start_f)
            count = 0
            span  = max(end - self.start_f, 1)

            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                if pos > end:
                    break
                if self.size != (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                 int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))):
                    frame = cv2.resize(frame, self.size)
                out.write(frame)
                count += 1
                self.progress.emit(int(count / span * 100))

            cap.release()
            out.release()
            self.finished.emit(True, self.dst)
        except Exception as e:
            self.finished.emit(False, str(e))


class VideoEditor(QWidget):
    frame_ready = pyqtSignal(object)   # emits np.ndarray (H,W,3) RGB on each video frame

    def __init__(self, recordings_dir: str = 'recordings', parent=None):
        super().__init__(parent)
        self.recordings_dir = recordings_dir
        self._current_file  = None
        self._total_frames  = 0
        self._fps           = 30.0
        self._export_thread = None
        self._last_frame_t  = 0.0
        self._loop          = False
        self._lib_paths: list[str] = []

        self._build_ui()
        self._refresh_clips()

    def _build_ui(self):
        self.setStyleSheet(f"""
            QWidget {{ background:{BG}; color:#d4aaaa; font-family:'Menlo','Monaco','SF Mono','Courier New'; }}
            QListWidget {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:6px; color:#c0c8d8; }}
            QListWidget::item:selected {{ background:{ACCENT}30; color:{ACCENT}; }}
            QPushButton {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:6px;
                           color:#c0c8d8; padding:5px 12px; font-size:12px; }}
            QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
            QPushButton:checked {{ background:{ACCENT}25; border-color:{ACCENT}; color:{ACCENT}; }}
            QSlider::groove:horizontal {{ background:{PANEL}; border:1px solid {BORDER}; height:4px; border-radius:2px; }}
            QSlider::handle:horizontal {{ background:{ACCENT}; width:12px; height:12px;
                                          border-radius:6px; margin:-4px 0; }}
            QSlider::sub-page:horizontal {{ background:{ACCENT}40; }}
            QLabel {{ color:#c0c8d8; }}
            QComboBox {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:6px;
                         color:#c0c8d8; padding:4px 8px; }}
            QProgressBar {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:4px; }}
            QProgressBar::chunk {{ background:{ACCENT}; border-radius:4px; }}
            QSplitter::handle {{ background: rgba(255,30,30,0.18); }}
            QSplitter::handle:horizontal {{ width: 6px; }}
            QSplitter::handle:vertical {{ height: 6px; }}
            QSplitter::handle:hover {{ background: rgba(255,30,30,0.45); }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        # Root horizontal splitter: clip list | preview+controls
        h_split = LiveSplitter(Qt.Orientation.Horizontal)
        h_split.setChildrenCollapsible(False)
        h_split.setHandleWidth(8)
        outer.addWidget(h_split, 1)

        # ── Left: vertical splitter — recorded clips (top) | library (bottom) ──
        left_w = QWidget()
        left   = QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)

        left_v = LiveSplitter(Qt.Orientation.Vertical)
        left_v.setChildrenCollapsible(False)
        left_v.setHandleWidth(8)
        left.addWidget(left_v, 1)

        # ── Recorded clips pane ───────────────────────────────────────────
        clips_w = QWidget()
        clips_l = QVBoxLayout(clips_w)
        clips_l.setContentsMargins(0, 0, 0, 4)
        clips_l.setSpacing(4)

        title = QLabel('RECORDED CLIPS')
        title.setStyleSheet(f'font-size:10px;font-weight:700;color:{ACCENT};letter-spacing:2px;')
        clips_l.addWidget(title)

        self.clip_list = QListWidget()
        self.clip_list.currentRowChanged.connect(self._on_clip_select)
        clips_l.addWidget(self.clip_list, 1)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton('Refresh')
        self.refresh_btn.clicked.connect(self._refresh_clips)
        self.import_btn  = QPushButton('Import')
        self.import_btn.clicked.connect(self._import_file)
        btn_row.addWidget(self.refresh_btn, 1)
        btn_row.addWidget(self.import_btn, 1)
        clips_l.addLayout(btn_row)

        self.delete_btn = QPushButton('Delete')
        self.delete_btn.setEnabled(False)
        self.delete_btn.setStyleSheet(
            f'QPushButton{{color:#FF3B30;border-color:rgba(255,59,48,0.3);}}'
            f'QPushButton:hover{{border-color:#FF3B30;background:rgba(255,59,48,0.15);}}'
            f'QPushButton:disabled{{color:#3a3a3a;border-color:{BORDER};}}'
        )
        self.delete_btn.clicked.connect(self._delete_clip)
        clips_l.addWidget(self.delete_btn)

        left_v.addWidget(clips_w)

        # ── Video library pane ────────────────────────────────────────────
        lib_w = QWidget()
        lib_l = QVBoxLayout(lib_w)
        lib_l.setContentsMargins(0, 4, 0, 0)
        lib_l.setSpacing(4)

        lib_title = QLabel('VIDEO LIBRARY')
        lib_title.setStyleSheet(f'font-size:10px;font-weight:700;color:{ACCENT};letter-spacing:2px;')
        lib_l.addWidget(lib_title)

        self.lib_list = QListWidget()
        self.lib_list.setToolTip('Load any video files for quick playback')
        self.lib_list.currentRowChanged.connect(self._on_lib_select)
        lib_l.addWidget(self.lib_list, 1)

        lib_btn_row = QHBoxLayout()
        self.lib_add_btn = QPushButton('+ Add')
        self.lib_add_btn.clicked.connect(self._lib_add)
        self.lib_remove_btn = QPushButton('Remove')
        self.lib_remove_btn.setEnabled(False)
        self.lib_remove_btn.clicked.connect(self._lib_remove)
        lib_btn_row.addWidget(self.lib_add_btn, 1)
        lib_btn_row.addWidget(self.lib_remove_btn, 1)
        lib_l.addLayout(lib_btn_row)

        left_v.addWidget(lib_w)
        left_v.setSizes([250, 150])

        # Deselect the other list when one gets focus
        self.clip_list.itemPressed.connect(lambda _: self.lib_list.clearSelection())
        self.lib_list.itemPressed.connect(lambda _: self.clip_list.clearSelection())

        h_split.addWidget(left_w)

        # ── Right: vertical splitter — preview on top, controls on bottom ─
        right_w = QWidget()
        right_l = QVBoxLayout(right_w)
        right_l.setContentsMargins(6, 0, 0, 0)
        right_l.setSpacing(0)

        v_split = LiveSplitter(Qt.Orientation.Vertical)
        v_split.setChildrenCollapsible(False)
        v_split.setHandleWidth(8)
        right_l.addWidget(v_split, 1)

        # Top pane: video preview
        preview_w = QWidget()
        preview_l = QVBoxLayout(preview_w)
        preview_l.setContentsMargins(0, 0, 0, 0)
        preview_l.setSpacing(4)

        self.preview_lbl = QLabel()
        self.preview_lbl.setMinimumHeight(80)
        self.preview_lbl.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Expanding)
        self.preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_lbl.setStyleSheet('background:black;border-radius:8px;')

        self._sink = QVideoSink()
        self.player    = QMediaPlayer()
        self.audio_out = QAudioOutput()
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoSink(self._sink)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self._sink.videoFrameChanged.connect(self._on_video_frame)
        preview_l.addWidget(self.preview_lbl)

        self.info_lbl = QLabel('No clip selected')
        self.info_lbl.setStyleSheet('font-size:11px;color:#6e7a8a;')
        preview_l.addWidget(self.info_lbl)

        v_split.addWidget(preview_w)

        # Bottom pane: timeline, trim, transport, export
        ctrl_w = QWidget()
        ctrl_l = QVBoxLayout(ctrl_w)
        ctrl_l.setContentsMargins(0, 6, 0, 0)
        ctrl_l.setSpacing(8)

        # Timeline slider
        tl_row = QHBoxLayout()
        self.pos_lbl = QLabel('0:00')
        self.pos_lbl.setFixedWidth(36)
        self.tl_slider = QSlider(Qt.Orientation.Horizontal)
        self.tl_slider.setRange(0, 0)
        self.tl_slider.sliderMoved.connect(self._seek)
        self.dur_lbl = QLabel('0:00')
        self.dur_lbl.setFixedWidth(36)
        tl_row.addWidget(self.pos_lbl)
        tl_row.addWidget(self.tl_slider)
        tl_row.addWidget(self.dur_lbl)
        ctrl_l.addLayout(tl_row)

        # In/Out points
        trim_row = QHBoxLayout()
        trim_row.addWidget(QLabel('In:'))
        self.in_slider = QSlider(Qt.Orientation.Horizontal)
        self.in_slider.setRange(0, 1000)
        self.in_slider.setValue(0)
        self.in_slider.setStyleSheet("QSlider::handle:horizontal { background:#34C759; }")
        trim_row.addWidget(self.in_slider)
        trim_row.addWidget(QLabel('Out:'))
        self.out_slider = QSlider(Qt.Orientation.Horizontal)
        self.out_slider.setRange(0, 1000)
        self.out_slider.setValue(1000)
        self.out_slider.setStyleSheet("QSlider::handle:horizontal { background:#FF3B30; }")
        trim_row.addWidget(self.out_slider)
        ctrl_l.addLayout(trim_row)

        # Transport controls
        transport = QHBoxLayout()
        self.play_btn = QPushButton('Play')
        self.play_btn.clicked.connect(self._toggle_play)
        self.stop_btn = QPushButton('Stop')
        self.stop_btn.clicked.connect(self._stop)
        self.loop_btn = QPushButton('Loop')
        self.loop_btn.setCheckable(True)
        self.loop_btn.setChecked(False)
        self.loop_btn.setToolTip('Loop between In and Out points')
        self.loop_btn.clicked.connect(self._toggle_loop)
        transport.addWidget(self.play_btn)
        transport.addWidget(self.stop_btn)
        transport.addWidget(self.loop_btn)
        transport.addStretch()
        ctrl_l.addLayout(transport)

        # Export controls
        exp_frame = QFrame()
        exp_frame.setStyleSheet(f'QFrame{{border:1px solid {BORDER};border-radius:8px;padding:4px;}}')
        exp_l = QVBoxLayout(exp_frame)
        exp_l.setSpacing(6)

        exp_title = QLabel('EXPORT')
        exp_title.setStyleSheet(f'font-size:10px;font-weight:700;color:{ACCENT};letter-spacing:2px;')
        exp_l.addWidget(exp_title)

        exp_opts = QHBoxLayout()
        exp_opts.addWidget(QLabel('Resolution:'))
        self.res_combo = QComboBox()
        self.res_combo.addItems(['1920×1080', '1280×720', '854×480', '640×360'])
        self.res_combo.setCurrentIndex(1)
        exp_opts.addWidget(self.res_combo)
        exp_opts.addWidget(QLabel('FPS:'))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(15, 60)
        self.fps_spin.setValue(30)
        self.fps_spin.setStyleSheet(f'background:{PANEL};border:1px solid {BORDER};color:#c0c8d8;border-radius:4px;padding:2px;')
        exp_opts.addWidget(self.fps_spin)
        exp_l.addLayout(exp_opts)

        self.export_btn = QPushButton('Export Trimmed Clip')
        self.export_btn.clicked.connect(self._export)
        self.export_btn.setStyleSheet(
            f'QPushButton{{background:{ACCENT}20;border:1px solid {ACCENT}60;color:{ACCENT};'
            f'border-radius:6px;padding:6px 12px;font-weight:600;}}'
            f'QPushButton:hover{{background:{ACCENT}40;}}'
        )
        exp_l.addWidget(self.export_btn)

        self.export_progress = QProgressBar()
        self.export_progress.setRange(0, 100)
        self.export_progress.setVisible(False)
        exp_l.addWidget(self.export_progress)

        self.export_status = QLabel('')
        self.export_status.setStyleSheet('font-size:11px;color:#6e7a8a;')
        exp_l.addWidget(self.export_status)

        ctrl_l.addWidget(exp_frame)
        ctrl_l.addStretch()
        v_split.addWidget(ctrl_w)

        h_split.addWidget(right_w)

        # Initial proportions — these are ratios; Qt scales to actual widget size
        h_split.setSizes([100, 200])
        v_split.setSizes([300, 200])

    # ── Video frame capture ───────────────────────────────────────────────────

    def _on_video_frame(self, frame: QVideoFrame):
        # Throttle to ~30 fps to avoid overloading the GL canvas
        now = time.monotonic()
        if now - self._last_frame_t < 1 / 31:
            return
        self._last_frame_t = now
        if not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        img = img.convertToFormat(QImage.Format.Format_RGB888)
        w, h   = img.width(), img.height()
        bpl    = img.bytesPerLine()          # bytes per row (may include padding)
        n_bytes = h * bpl
        try:
            ba = img.bits()
            ba.setsize(n_bytes)
            raw = np.frombuffer(ba, dtype=np.uint8).reshape(h, bpl)
            arr = raw[:, : w * 3].reshape(h, w, 3).copy()
        except Exception as e:
            print(f"[VideoEditor] frame→numpy failed: {e}")
            return
        # Update the preview label
        try:
            pw = max(self.preview_lbl.width(),  1)
            ph = max(self.preview_lbl.height(), 1)
            scaled = img.scaled(pw, ph,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            self.preview_lbl.setPixmap(QPixmap.fromImage(scaled))
        except Exception:
            pass
        # Send to GL canvas
        self.frame_ready.emit(arr)

    # ── Clip list management ──────────────────────────────────────────────────

    def _refresh_clips(self):
        self.clip_list.clear()
        if not os.path.isdir(self.recordings_dir):
            return
        files = sorted(
            [f for f in os.listdir(self.recordings_dir) if f.endswith(('.mp4', '.avi', '.mov'))],
            reverse=True
        )
        for f in files:
            self.clip_list.addItem(QListWidgetItem(f))

    def _delete_clip(self):
        row  = self.clip_list.currentRow()
        item = self.clip_list.item(row)
        if not item:
            return
        path = os.path.join(self.recordings_dir, item.text())
        reply = QMessageBox.question(
            self, 'Delete clip',
            f'Delete {item.text()}?\nThis cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Stop playback if this file is currently loaded
        if self._current_file == path:
            self.player.stop()
            self._current_file = None
            self.info_lbl.setText('No clip selected')
        try:
            os.remove(path)
        except OSError as e:
            QMessageBox.warning(self, 'Delete failed', str(e))
            return
        self._refresh_clips()
        self.delete_btn.setEnabled(False)

    def _import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Import Video', '',
            'Video Files (*.mp4 *.mov *.avi *.mkv);;All Files (*)'
        )
        if path:
            self._load_clip(path)

    # ── Video library ─────────────────────────────────────────────────────────

    def _lib_add(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, 'Add to Library', '',
            'Video Files (*.mp4 *.mov *.avi *.mkv *.webm);;All Files (*)'
        )
        for path in paths:
            if path not in self._lib_paths:
                self._lib_paths.append(path)
                self.lib_list.addItem(QListWidgetItem(Path(path).name))

    def _lib_remove(self):
        row = self.lib_list.currentRow()
        if row < 0:
            return
        self._lib_paths.pop(row)
        self.lib_list.blockSignals(True)
        self.lib_list.takeItem(row)
        self.lib_list.blockSignals(False)
        self.lib_list.setCurrentRow(-1)
        self.lib_remove_btn.setEnabled(False)

    def _on_lib_select(self, row):
        self.lib_remove_btn.setEnabled(row >= 0)
        if row < 0 or row >= len(self._lib_paths):
            return
        self._load_clip(self._lib_paths[row])

    def _on_clip_select(self, row):
        self.delete_btn.setEnabled(row >= 0)
        if row < 0:
            return
        item = self.clip_list.item(row)
        if item:
            self.lib_list.clearSelection()
            path = os.path.join(self.recordings_dir, item.text())
            self._load_clip(path)

    def _load_clip(self, path: str):
        self._current_file = path
        self.player.setSource(QUrl.fromLocalFile(path))

        if CV2_OK:
            cap = cv2.VideoCapture(path)
            self._total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self._fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()
            dur_s  = self._total_frames / self._fps
            self.info_lbl.setText(f'{Path(path).name}  ·  {w}×{h}  ·  {dur_s:.1f}s  ·  {self._fps:.0f}fps')

        self.in_slider.setValue(0)
        self.out_slider.setValue(1000)

    # ── Playback ──────────────────────────────────────────────────────────────

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText('Play')
        else:
            self.player.play()
            self.play_btn.setText('Pause')

    def _stop(self):
        self.player.stop()
        self.play_btn.setText('Play')

    def _toggle_loop(self, checked: bool):
        self._loop = checked

    def _seek(self, value):
        dur = self.player.duration()
        if dur > 0:
            self.player.setPosition(int(value / 1000 * dur))

    def _in_ms(self) -> int:
        dur = self.player.duration()
        return int(self.in_slider.value() / 1000 * dur) if dur > 0 else 0

    def _out_ms(self) -> int:
        dur = self.player.duration()
        return int(self.out_slider.value() / 1000 * dur) if dur > 0 else 0

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._loop:
            self.player.setPosition(self._in_ms())
            self.player.play()
            self.play_btn.setText('Pause')

    def _on_position(self, ms):
        dur = self.player.duration()
        if dur > 0:
            # Enforce out-point when looping (only if out-point is actually trimmed)
            if self._loop and self.out_slider.value() < 1000:
                out = self._out_ms()
                in_ = self._in_ms()
                if ms >= out and in_ < out:
                    self.player.setPosition(in_)
                    return
            self.tl_slider.setValue(int(ms / dur * 1000))
        s = ms // 1000
        self.pos_lbl.setText(f'{s//60}:{s%60:02d}')

    def _on_duration(self, ms):
        self.tl_slider.setRange(0, 1000)
        s = ms // 1000
        self.dur_lbl.setText(f'{s//60}:{s%60:02d}')

    # ── Export ────────────────────────────────────────────────────────────────

    def _export(self):
        if not self._current_file:
            QMessageBox.information(self, 'No clip', 'Please select a clip first.')
            return

        path, _ = QFileDialog.getSaveFileName(
            self, 'Export As', str(Path(self._current_file).stem) + '_export.mp4',
            'MP4 Video (*.mp4)'
        )
        if not path:
            return

        in_f  = int(self.in_slider.value()  / 1000 * self._total_frames)
        out_f = int(self.out_slider.value() / 1000 * self._total_frames)

        res_txt = self.res_combo.currentText().replace('×', 'x').split('x')
        size    = (int(res_txt[0]), int(res_txt[1]))
        fps     = self.fps_spin.value()

        self.export_progress.setVisible(True)
        self.export_progress.setValue(0)
        self.export_btn.setEnabled(False)

        self._export_thread = ExportThread(self._current_file, path, in_f, out_f, fps, size)
        self._export_thread.progress.connect(self.export_progress.setValue)
        self._export_thread.finished.connect(self._on_export_done)
        self._export_thread.start()

    def _on_export_done(self, ok, msg):
        self.export_btn.setEnabled(True)
        self.export_progress.setVisible(False)
        if ok:
            self.export_status.setText(f'Exported: {Path(msg).name}')
            self._refresh_clips()
        else:
            self.export_status.setText(f'Error: {msg}')

    def add_clip(self, path: str):
        """Called when a new recording is saved."""
        self._refresh_clips()
