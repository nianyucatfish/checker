import os
import soundfile as sf
import sounddevice as sd
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtCore import pyqtSignal, Qt, QThread
import os
import struct
import csv
from io import StringIO
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTextEdit,
    QStackedWidget,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QPushButton,
    QHBoxLayout,
)
from PyQt6.QtGui import (
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QColor,
    QKeySequence,
    QPainter,
    QBrush,
    QPen,
)
import numpy as np
import librosa

# 已移除图形 MIDI 预览依赖 (mido, pyqtgraph)。
from audio_player import MediaPlayer
import os
from PyQt6.QtGui import QFileSystemModel, QColor, QFont
from PyQt6.QtCore import Qt
import os
import re
import soundfile as sf
import os
import shutil
import json
from datetime import datetime
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QTreeView,
    QTextEdit,
    QSplitter,
    QListWidget,
    QTabWidget,
    QLabel,
    QToolBar,
    QMessageBox,
    QMenu,
    QInputDialog,
    QListWidgetItem,
    QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer, QFileSystemWatcher, QPoint
from PyQt6.QtGui import QAction, QKeySequence, QColor


import librosa
import numpy as np
import resampy
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import librosa
import numpy as np
import sounddevice as sd
import soundfile as sf
from PyQt6.QtCore import (
    Qt,
    QEvent,
    QThread,
    QTimer,
    pyqtSignal,
    QObject,
    QRunnable,
    QThreadPool,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

import pyqtgraph as pg
import resampy


import os
from PyQt6.QtCore import QThread, pyqtSignal
from logic_checker import LogicChecker
