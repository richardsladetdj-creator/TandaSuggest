"""Dialog displaying noise diagnosis report for a suggestion track."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextEdit, QVBoxLayout

from tanda_suggester.tandas import NoiseReport


class NoiseReportDialog(QDialog):
    def __init__(
        self,
        track_title: str,
        track_artist: str,
        reports: list[NoiseReport],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Noise Diagnosis")
        self.resize(720, 520)
        self._build_ui(track_title, track_artist, reports)

    def _build_ui(
        self, track_title: str, track_artist: str, reports: list[NoiseReport]
    ) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        text = QTextEdit()
        text.setReadOnly(True)
        font = QFont("Menlo")
        if not font.exactMatch():
            font = QFont("Courier New")
        font.setPointSize(12)
        text.setFont(font)
        text.setPlainText(self._render(track_title, track_artist, reports))
        layout.addWidget(text)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

    def _render(
        self, track_title: str, track_artist: str, reports: list[NoiseReport]
    ) -> str:
        lines: list[str] = []
        lines.append(f'Diagnosing: "{track_title}" [{track_artist}]')
        lines.append("")

        if not reports:
            lines.append("No mixed-orchestra tandas found for this track.")
            lines.append("")
            lines.append(
                "The track appears only in homogeneous tandas (single orchestra)."
            )
            lines.append(
                "Noise may have another cause, such as a genre mis-tag."
            )
            return "\n".join(lines)

        for report in reports:
            lines.append(
                f"Playlist: {report.playlist_name}  (tanda #{report.tanda_position})"
            )
            lines.append("─" * 64)

            # Flatten and sort all tracks by playlist position
            all_tracks: list[tuple[int, str, str]] = []
            for group in report.orchestra_groups:
                all_tracks.extend(group.tracks)
            all_tracks.sort(key=lambda t: t[0])

            missing_set = set(report.missing_cortina_positions)

            for pl_pos, title, artist in all_tracks:
                title_col = title if len(title) <= 40 else title[:37] + "…"
                lines.append(f"  [{pl_pos:>3}]  {title_col:<42} {artist}")
                if pl_pos in missing_set:
                    marker = f"↑ Missing cortina after position {pl_pos} ↑"
                    lines.append(f"  {marker:^62}")

            lines.append("")

        return "\n".join(lines)
