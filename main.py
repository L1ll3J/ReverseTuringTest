import os
import random
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from dev_trace import TraceRun, call_with_trace, prompt_fingerprints, source_fingerprint, development_mode
from ai_context import CharacterContext, DialogueLine, MemoryRecord, PersonPresent, PublicState, SelfIdentity, SpeechProposal
from ai_runtime import AIRuntime, AIRequestError
from dictation import DictationRecorder, DictationError
from preferences import Preferences
from input_bindings import BindingBadge, BindingSettingsPage, DictationBindings
from model_config_ui import ModelConfigPanel
from conversation_flow import DirectReplyPolicy, GroupTurnSnapshot, TurnPreparation
from conversation_widgets import DialogueView, ConversationStage, StageParticipant, character_art_path
from message_view import DisplayRecord, MessageLog
from house_view import HouseView, RoomObservation, ROOM_LABELS, ARTWORK_SIZE
from night_arrivals import NightArrivals, assign_guest_rooms, flight_destination, room_capacity
from overlay_host import OverlayHost
from unlock_ui import UnlockPage, unlock_presentation
from npc_items import NPCItemState, NPC_ITEM_REGISTRY
from items import ITEM_CATALOGUE, Inventory, DraftState, InventoryError
from game_rules import BodyState, DeathCause, ItemUseContext, NightMemoryView, PlayerItemRules, ItemRuleError, GUARDIAN_PUBLIC, SOUL_PRIVATE
from identity_state import IdentityState
from soul_rules import SoulResolver
from voodoo_rules import VoodooResolver
from presentation_aliases import PresentationAliases
from love_potion import LoveDose, LovePotionHistory
from muffled import TONES, TONE_LABELS, muffled_sound
from progression import ProfileStore, ProfileSnapshot, ProgressEvent, ProfileError
from content import CONTENT_CATALOGUE, UNLOCK_GOALS, available_content
from item_ui import InlineItemDraftPage, InventoryPage, GameComboBox
from item_catalogue_ui import ItemCataloguePanel
import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QTextEdit, QLineEdit, QLabel, QHBoxLayout, QGridLayout, QSlider, QStackedLayout, QGraphicsDropShadowEffect, QToolButton, QFrame, QDialog, QDialogButtonBox, QComboBox, QSizePolicy, QScrollArea, QStyle, QStyleOptionButton
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer, QPoint, QUrl, QEvent
from PyQt5.QtGui import QColor, QTextCursor, QTextCharFormat, QFontDatabase, QFont, QPixmap, QIcon, QGuiApplication, QTextBlockFormat, QTextOption, QPalette, QRegion
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

# Mechanics are integrated separately from approval of NPC-facing narration.
OPTIONAL_ITEM_NARRATION_PENDING = frozenset()


@dataclass(frozen=True)
class ItemDeathRecord:
    attempt_id: str
    body_id: str
    day: int
    cause: DeathCause
    room_id: str | None
    observed_name: str | None = None


def resource_path(relative_path):
    root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, relative_path)


def load_system_prompt():
    with open(resource_path("System Prompt.txt"), "r", encoding="utf-8") as file:
        return file.read()


def readable_body_font():
    font = QFont("Segoe UI") if sys.platform == "win32" else QFontDatabase.systemFont(QFontDatabase.GeneralFont)
    font.setPixelSize(18)
    return font


def fit_window_to_screen(window, preferred_width, preferred_height):
    screen = window.screen() or QGuiApplication.primaryScreen()
    available = screen.availableGeometry()
    width = min(preferred_width, max(1, int(available.width() * 0.9)))
    height = min(preferred_height, max(1, int(available.height() * 0.9)))
    window.resize(width, height)
    window.move(available.x() + (available.width() - width) // 2,
                available.y() + (available.height() - height) // 2)


def vote_conversation_minimum():
    if os.getenv("ALIBAI_DEV_MODE") != "1":
        return 20
    try:
        minimum = int(os.getenv("ALIBAI_DEV_VOTE_MINIMUM", "20"))
    except ValueError:
        return 20
    return minimum if minimum >= 2 else 20


def validate_vote(voter, vote, characters, player_name):
    if not isinstance(vote, str) or vote == voter:
        return "NONE"
    if vote == "NONE" or vote == player_name or characters.get(vote, {}).get("alive", False):
        return vote
    return "NONE"


def parallel_requests(operation, contexts, *, trace_scope=None):
    if not contexts:
        return {}
    with ThreadPoolExecutor(max_workers=len(contexts)) as executor:
        futures = {context.character: executor.submit(call_with_trace, operation, context, trace_scope=trace_scope) for context in contexts}
        return {character: future.result() for character, future in futures.items()}


class AIWorker(QThread):
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            result = self.operation()
        except AIRequestError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("The response could not be completed. Please retry.")
        else:
            self.result_ready.emit(result)


class GameWindow(QMainWindow):
    development_trace_notice = pyqtSignal(str)
    history_record_added = pyqtSignal(object)
    night_arrival_resolved = pyqtSignal(object)

    def __init__(self):
        super().__init__()  # Ensure the parent class is initialized
        self.sfx_enabled = True
        self.effect_player = QMediaPlayer()
        self.effect_player.setVolume(25)
        self.music_enabled = True

        # Initialize game state variables first
        self.player_name = ""
        self.preferences = Preferences()
        self.entered_player_name = self.preferences.last_player_name()
        self.characters = {}
        self.rooms = ["Attic", "Kitchen", "Library", "Basement", "Cellar"]
        self.old_crime_scenes = []
        self.room_assignments = {}
        self.night_arrivals = None
        self._night_known_alive_bodies = None
        self._arrival_entry_events = set()
        self._arrival_memory_receipts = set()
        self._disclosed_item_deaths = set()
        self._arrival_known_corpse_rooms = {}
        self._arrival_observed_bodies = {room: [] for room in self.rooms}
        self._player_entry_receipts = set()
        self._arrival_finalized = False
        self._room_choice_in_progress = False
        self.round_number = 0
        self.phase = "start"
        self.conversation_length = 0  # To track when the "Call Vote" button should appear
        self.show_room_allocations = True  # Track mode for room allocation display
        self.game_over = False
        self.active_workers = []
        self.votes = {}  # To store all votes (including player's)
        self.vote_buttons_widget = None  # Widget for player vote buttons
        self.voting_resolved = False

        # New Variables for the conversation phase
        self.history_records = []
        self.public_event_display_records = []
        self.display_sequence = 0
        self.current_conversation = []  # Holds the conversation log
        self.most_recent_messageS = []  # Holds the short-term memory stack of recent messages
        self.alive_characters = []  # Holds the list of alive characters
        self.NPCs = []  # Holds the list of NPC characters
        self.minimum_to_call_vote = vote_conversation_minimum()
        self.trace = TraceRun(on_error=self.development_trace_notice.emit)
        self.development_trace_notice.connect(self.show_development_trace_notice)
        self.trace_callback_scope = None
        self.last_completed_trace_job_id = None
        self.ai_runtime = AIRuntime(prompt_root=os.path.dirname(resource_path("System Prompt.txt")), trace=self.trace)
        self.dictation_recorder = DictationRecorder(self, device_name=self.preferences.microphone_name())
        self.dictation_session = 0
        self.dictation_context = None
        self.dictation_worker = None
        self.dictation_recorder.started.connect(self.handle_dictation_started)
        self.dictation_recorder.ready.connect(self.transcribe_recording)
        self.dictation_recorder.failed.connect(self.handle_dictation_failure)
        self.dictation_recorder.diagnostic.connect(self.record_dictation_diagnostic)
        self.public_events = []
        self.public_dialogue = []
        self.npc_recall_public_event_start = 0
        self.npc_recall_dialogue_start = 0
        self.night_rooms = tuple(self.rooms)
        self.ai_busy = False
        self.request_serial = 0
        self.retry_action = None
        self.direct_replies = DirectReplyPolicy()
        self.pending_direct_reply = None
        self.required_reply_anchor = None
        self.last_speech_origin = None
        self.turn_preparation = TurnPreparation()
        self.conversation_revision = 0
        self.awaiting_preparation = None
        self.prepared_scope = None
        self.prepared_retry_snapshot = None
        self.npc_items = NPCItemState()
        self.profile_store = ProfileStore(os.getenv("ALIBAI_PROFILE_PATH") or None, CONTENT_CATALOGUE)
        self.profile = ProfileSnapshot()
        self.run_id = uuid4().hex
        self.player_owner_id = "player"
        self.body_ids = {}
        self.identity_state = None
        self.presentation_aliases = PresentationAliases()
        self.stage_body_order = []
        self.dialogue_perceptions = {}
        self.mind_data = {}
        self.body_weapons = {}
        self.mind_weapons = {}
        self.body_muffle_styles = {}
        self.player_alive = True
        self.inventory = Inventory(self.player_owner_id)
        self.item_rules = PlayerItemRules(self.player_owner_id, self.inventory, guardian_scope="body")
        self.item_activation_contexts = {}
        self.soul_resolver = SoulResolver()
        self.voodoo_resolver = VoodooResolver()
        self.soul_former_bodies = {}
        self.item_drafts = None
        self.draft_page = None
        self._draft_room_selection_visible = False
        self._draft_message_log_visible = False
        self.inventory_page = None
        self.transcript_page = None
        self.help_page = None
        self.pending_room_action = None
        self.run_weapons = ()
        self.run_item_pool = ()
        self.progress_sequence = 0
        self.pending_progress = []
        self.items_used = 0
        self.used_item_types = set()
        self.applied_item_events = set()
        self.applied_death_attempts = set()
        self.item_deaths = {}
        self.published_item_deaths = set()
        self.morning_item_notes = []
        self.night_memory_views = ()
        self.guardian_intervened_this_run = False
        self.coin_survived_this_run = False
        self.bonus_action_pending = False
        self.bonus_action_id = None
        self.run_player_kills = 0
        self.innocents_executed = 0
        self.witness_body_ids = set()
        self.night_attacks = []
        self._night_memories_written = False
        self.vote_id = None
        self.vote_snapshot = None

        # Set a consistent window size
        self.resize(800, 600)

        #WEAPONS
        self.selected_weapon = None           
        self.weapon_pen_backfire = 0.1     
        self.pending_pen_note_text = None          
        self.black_magic_controller = None
        self.black_magic_target = None
        self.knife_extra_target = None      
        self.knife_used_bloodlust = False
        self.c4_target_room = None
        self.c4_cooldown_nights = 0
        self.player_sleep_room = None          
        self.voodoo_current_identity = None    
        self._c4_exploded_last_night = False
        self.stopwatch_selected = False         
        self.victims_last_night = []
        self._override_murder_room = None          
        self.pen_last_victim_name = None 

        self.weapon_action_panel = None
        self._weapon_windows = {"pen": None, "blackmagic": None, "knife": None, "c4": None}

        

        # weapon registry (easy to extend)
        self.weapons = [
            {"id": "bare", "name": "Bare Hands", "desc": "No effect, no gimmick. \n A hardcore killer's weapon of choice.", "image": "icons/weapons/bare.png"},
            {"id": "pen", "name": "Pen", "desc": "Before a kill, write a fake 'last note' your victim will be found with. "+ 
                                                  f"{int(self.weapon_pen_backfire*100)}% chance to implicate yourself.", "image": "icons/weapons/pen.png"}, 
            {"id": "knife", "name": "Knife",
            "desc": "Gain the ability to kill twice per night, but your bloodlust will be visible.",
            "image": "icons/weapons/knife.png"},

            {"id": "c4", "name": "C4",
            "desc": "Not very subtle. \n Allows you to kill from a distance. \n One night cooldown.",
            "image": "icons/weapons/c4.png"},

            {"id": "stopwatch", "name": "Stopwatch",
            "desc": "Witnesses to night kills will vouch for you instead of accuse.",
            "image": "icons/weapons/polished-stopwatch.png"},
            {"id": "voodoo", "name": "Voodoo Blade", "desc": "Possess the body of whoever you stab; the body you previously inhabited is the one that is found dead.", "image": "icons/weapons/polished-voodoo.png"},
            {"id": "blackmagic", "name": "Dark Magic", "desc": "Mind-control someone to kill for you each night, but they'll know it was you.", "image": "icons/weapons/polished-blackmagic.png"},
            {"id": "love", "name": "Love",
            "desc": "\n Can't we all just get along?",
            "image": "icons/weapons/love.png"}
        ]


        # For ESC menu
        self._esc_overlay = None
        self._esc_sound_btn = None

        # Call init_ui after initializing game variables
        self.init_ui()


    def body_id(self, label):
        return self.body_ids.setdefault(label, uuid4().hex)

    def ensure_body_ids(self):
        for label in (*self.characters, self.player_name):
            if label:
                self.body_id(label)

    def ensure_identity_state(self):
        self.ensure_body_ids()
        if self.identity_state is None and self.player_name:
            self.identity_state = IdentityState.from_bodies(
                self.player_owner_id, self.body_id(self.player_name),
                (self.body_id(name) for name in self.characters if name != self.player_name),
                appearance_names={self.body_id(name): name for name in (*self.characters, self.player_name)})
            self.mind_data = {self.mind_id(name): data for name, data in self.characters.items()}
            self.mind_data[self.player_owner_id] = {"profile": [], "experiences": [], "dispositions": []}
            self.body_muffle_styles = {self.body_id(name): data.get("muffle_style", "fallback")
                                       for name, data in self.characters.items()}

    def mind_id(self, label):
        self.ensure_identity_state()
        return self.identity_state.mind_for_body(self.body_id(label)) if self.identity_state else None

    def label_for_mind(self, mind_id):
        self.ensure_identity_state()
        return self.label_for_body(self.identity_state.body_for_mind(mind_id)) if self.identity_state else None

    def exchange_player_mind(self, target_body_id, *, resolved_identity=None):
        """Install an exchange; callers provide the separately approved narration.

        Private data follows its mind. Body labels and recorded events remain literal.
        """
        self.ensure_identity_state()
        target = next((body for body in self.body_states() if body.body_id == target_body_id), None)
        if (target is None or not target.alive or not target.is_npc or not self.player_alive
                or self.ai_busy or self.game_over or self.phase not in {"action", "conversation"}):
            raise ItemRuleError("An exchange requires two living, available minds")
        old_label = self.player_name
        old_body_id = self.body_id(old_label)
        old_state = self.identity_state
        new_state = old_state.exchange(target_body_id)
        if resolved_identity is not None and resolved_identity != new_state:
            raise ItemRuleError("The resolved exchange no longer matches the current identity")
        memory_views = tuple(
            NightMemoryView(new_state.body_for_mind(old_state.mind_for_body(view.body_id)),
                            view.night, view.experiences) for view in self.night_memory_views)
        target_data = self.characters.pop(target.label)
        old_style = self.body_muffle_styles.get(old_body_id, "fallback")
        target_data["muffle_style"] = old_style
        target_data["alive"] = True
        self.characters[old_label] = target_data
        self.night_memory_views = memory_views
        witness_minds = {old_state.mind_for_body(body_id) for body_id in self.witness_body_ids}
        self.witness_body_ids = {new_state.body_for_mind(mind_id) for mind_id in witness_minds
                                 if mind_id is not None and mind_id != self.player_owner_id}
        self.identity_state = new_state
        self.player_name = target.label
        self.invalidate_prepared_turn("identity_changed", changed=True)
        self.request_serial += 1
        self.retry_action = None
        self.required_reply_anchor = None
        self.prepared_retry_snapshot = None
        self.cancel_dictation()
        self.interject_input.clear()
        self.retire_weapon_editors()
        self.pending_pen_note_text = None
        self.black_magic_controller = None
        self.black_magic_target = None
        self.knife_extra_target = None
        self.c4_target_room = None
        weapon = self.selected_weapon
        if weapon:
            if weapon["id"] == "voodoo":
                self.mind_weapons[self.player_owner_id] = weapon
            elif weapon["id"] != "bare":
                self.body_weapons[old_body_id] = weapon
        self.selected_weapon = (self.mind_weapons.get(self.player_owner_id)
                                or self.body_weapons.pop(target_body_id, None)
                                or next(weapon for weapon in self.weapons if weapon["id"] == "bare"))
        self.stopwatch_selected = self.selected_weapon["id"] == "stopwatch"
        self.refresh_recipient_choices()
        self.refresh_conversation_stage()
        self.trace_action("identity_changed", previous_body=old_label, current_body=target.label)
        return new_state

    def apply_soul_exchange(self, event):
        self.ensure_identity_state()
        resolution = self.soul_resolver.resolve(
            event.event_id, self.identity_state, event.actor_body_id,
            event.target_body_id, self.body_states(), event.day)
        if resolution.application_state == "superseded":
            return
        if resolution.identity_application_required:
            self.exchange_player_mind(event.target_body_id, resolved_identity=resolution.identity)
        data = self.mind_data[resolution.target_mind_id]
        self.remember_private(
            data, self.label_for_mind(resolution.target_mind_id), "experience",
            SOUL_PRIVATE.format(new_body=resolution.old_body_name,
                                original_name=resolution.target_original_name))
        self.soul_former_bodies[event.event_id] = (resolution.old_body_id, resolution.following_night)
        self.record_history(f"You exchanged bodies with {resolution.new_body_name}.", display=True)
        self.trace.record("soul_exchange_resolved", {"event_id": event.event_id, "resolution": resolution})

    def perceived_name(self, body_id):
        self.ensure_identity_state()
        return ((self.identity_state.appearance_name(body_id) if self.identity_state else None)
                or self.label_for_body(body_id))

    def perceived_actor(self, handle):
        return self.perceived_name(self.body_ids[handle]) if handle in self.body_ids else handle

    def display_label(self, body_id):
        name = self.perceived_name(body_id)
        if self.phase not in {"conversation", "voting"}:
            return name
        present = tuple(body.body_id for body in self.body_states() if body.alive)
        if body_id not in present:
            return name
        snapshot = self.presentation_aliases.for_presence(
            f"{self.run_id}:{self.round_number}:discussion", self.identity_state, present)
        return snapshot.label_for_body(body_id)

    def display_actor(self, handle):
        return self.display_label(self.body_ids[handle]) if handle in self.body_ids else handle

    def room_actor_labels(self, room, handles, *, include_player=False):
        """Project only supplied observed actors, with explicit local player presence."""
        self.ensure_identity_state()
        handles = tuple(handles)
        present = [self.body_id(handle) for handle in handles]
        if include_player and self.player_alive and self.body_id(self.player_name) not in present:
            present.append(self.body_id(self.player_name))
        aliases = self.presentation_aliases.for_presence(
            f"{self.run_id}:{self.round_number}:room:{room}", self.identity_state, present)
        return tuple(aliases.label_for_body(self.body_id(handle)) for handle in handles)

    def populate_body_choices(self, combo, handles, selected=None):
        for handle in handles:
            combo.addItem(self.perceived_actor(handle), self.body_id(handle))
        if selected in handles:
            combo.setCurrentIndex(combo.findData(self.body_id(selected)))

    def body_states(self):
        self.ensure_body_ids()
        states = [BodyState(self.body_ids[name], name, bool(data["alive"]), True,
                            mind_id=self.mind_id(name) if hasattr(self, "identity_state") else self.body_ids[name],
                            appearance_name=self.perceived_name(self.body_ids[name]))
                  for name, data in self.characters.items() if name != self.player_name]
        if self.player_name:
            states.append(BodyState(self.body_ids[self.player_name], self.player_name, self.player_alive, False,
                                    mind_id=self.player_owner_id,
                                    appearance_name=self.perceived_name(self.body_ids[self.player_name])))
        return tuple(states)

    def label_for_body(self, body_id):
        return next((label for label, identifier in self.body_ids.items() if identifier == body_id), None)

    def load_profile(self):
        try:
            self.profile = self.profile_store.read()
        except ProfileError as error:
            self.show_profile_error(str(error))
            return False
        return True

    def show_profile_error(self, message):
        self.progress_notice.setText(message + " Your game can continue. Existing progress has been preserved.")
        self.progress_notice.show()
        self.progress_retry_button.show()

    def queue_progress(self, event_type):
        event_types = [event_type]
        if event_type == "player_kill":
            self.run_player_kills += 1
            if self.run_player_kills == 3:
                event_types.append("three_kills_one_run")
        for queued_type in event_types:
            self.progress_sequence += 1
            event = ProgressEvent(self.run_id, self.progress_sequence, queued_type)
            self.pending_progress.append(event)
            self.trace.record("progress_queued", {"event": event})
        self.flush_progress()

    def flush_progress(self):
        unlocked = []
        while self.pending_progress:
            try:
                update = self.profile_store.record(self.pending_progress[0])
            except ProfileError as error:
                self.enqueue_unlock_celebrations(unlocked)
                self.show_profile_error(str(error))
                return False
            self.profile = update.profile
            self.trace.record("progress_saved", {"event": self.pending_progress[0],
                                                 "newly_unlocked": update.newly_unlocked,
                                                 "applied": update.applied})
            unlocked.extend(update.newly_unlocked)
            self.pending_progress.pop(0)
        had_failure = not self.progress_retry_button.isHidden()
        self.progress_retry_button.hide()
        self.enqueue_unlock_celebrations(unlocked)
        if had_failure:
            self.progress_notice.setText("Progress saved.")
            self.progress_notice.show()
        return True

    def enqueue_unlock_celebrations(self, unlocked):
        # ProfileStore is authoritative: never infer unlocks from loaded profiles.
        for content_id in unlocked:
            self.overlay_host.enqueue_unlock(
                lambda key=content_id: self.build_unlock_page(key), title="New Unlock!")

    def build_unlock_page(self, content_id):
        presentation = unlock_presentation(content_id, self.weapons, ITEM_CATALOGUE)
        page = UnlockPage(presentation, self.overlay_host.close_page,
                          body_font=readable_body_font(), title_font=self.speaker_font)
        fit_window_to_screen(self.overlay_host, 620, 560)
        return page

    def unlock_celebrations_ready(self):
        return (self.phase not in ("weapon_select", "restarting", "closed")
                and self.pending_room_action is None and not self.separate_surface_open())

    def show_unlock_error(self, message):
        # Presentation failures must not suggest retrying already persisted events.
        notice = message + " Your unlock is saved and available next run."
        if not self.progress_retry_button.isHidden():
            notice = self.progress_notice.text() + " " + notice
        self.progress_notice.setText(notice)
        self.progress_notice.show()

    def retry_progress(self):
        if self.flush_progress() and self.load_profile():
            self.progress_retry_button.hide()
            if self.phase == "weapon_select":
                self.show_weapon_select_screen()
            elif self.phase == "start":
                self.item_catalogue.refresh(self.profile)

    def show_menu_tab(self, tab):
        if self.phase != "start" or tab not in ("play", "items"):
            return
        if tab == "items":
            self.load_profile()
            self.item_catalogue.refresh(self.profile)
        self.menu_stack.setCurrentWidget(self.item_catalogue_page if tab == "items" else self.play_page)
        self.play_tab_button.setChecked(tab == "play")
        self.items_tab_button.setChecked(tab == "items")

    def set_menu_item_enabled(self, item_id, enabled):
        if self.phase != "start" or self.menu_stack.currentWidget() is not self.item_catalogue_page:
            raise ProfileError("Change item-pool settings from the main menu's Items tab.")
        try:
            self.profile = self.profile_store.set_item_enabled(item_id, enabled)
        except ProfileError as error:
            self.show_profile_error(str(error))
            raise
        if not self.pending_progress and not self.progress_retry_button.isHidden():
            self.progress_retry_button.hide()
            self.progress_notice.hide()
        return self.profile

    def offer_room_item(self, room, continuation):
        # Only an explicit item choice or Leave resumes the accepted room action.
        if self.phase != "action" or self.game_over or self.pending_room_action is not None:
            return
        offer = self.item_drafts.enter_room(room, self.inventory.snapshot()) if self.item_drafts else None
        self.retire_weapon_editors()
        if offer is None:
            continuation()
            return
        self.pending_room_action = continuation
        self._draft_room_selection_visible = not self.room_buttons_widget.isHidden()
        self._draft_message_log_visible = not self.message_log.isHidden()
        self.trace_action("item_offer_created", offer=offer)
        self.set_room_buttons_enabled(False)
        self.room_buttons_widget.hide()
        self.message_log.hide()
        self.weapon_action_panel.hide()
        self.show_item_offer(offer)

    def show_item_offer(self, offer):
        self.retire_item_offer_page()
        self.draft_page = InlineItemDraftPage(
            offer, ITEM_CATALOGUE, self.choose_item_offer,
            self.reroll_item_offer, self.skip_item_offer,
            reroll_count=self.item_drafts.rerolls_remaining,
            can_reroll={slot: bool(self.item_drafts.reroll_alternatives(
                offer.offer_id, self.inventory.snapshot(), slot_index=slot)) for slot in range(len(offer.options))},
            picks_remaining=offer.picks_remaining,
            body_font=readable_body_font(), title_font=self.speaker_font,
        )
        self.layout.insertWidget(self.layout.indexOf(self.room_buttons_widget), self.draft_page, 1)
        self.draft_page.setMaximumWidth(1280)
        self.draft_page.setMaximumHeight(720)
        self.layout.setAlignment(self.draft_page, Qt.AlignCenter)
        self.draft_page.show()
        self.layout.activate()
        self.position_corner_controls()
        self.draft_page.take_buttons[0].setFocus(Qt.OtherFocusReason)

    def item_offer_is_current(self, offer_id):
        offer = self.item_drafts.pending_offer if self.item_drafts is not None else None
        return (self.phase == "action" and not self.game_over
                and self.pending_room_action is not None
                and offer is not None and offer.offer_id == offer_id)

    def retire_item_offer_page(self):
        page = self.draft_page
        self.draft_page = None
        if page is not None:
            self.layout.removeWidget(page)
            page.hide()
            # A choice or reroll may still be executing this page's signal.
            page.deleteLater()

    def cancel_item_offer(self):
        """Invalidate an obsolete phase's offer without accepting its room action."""
        self.pending_room_action = None
        self._draft_room_selection_visible = False
        self._draft_message_log_visible = False
        if self.item_drafts is not None and self.item_drafts.pending_offer is not None:
            self.item_drafts.skip(self.item_drafts.pending_offer.offer_id)
        self.retire_item_offer_page()
        self.overlay_host.resume_unlocks()

    def choose_item_offer(self, offer_id, slot):
        if not self.item_offer_is_current(offer_id):
            return
        try:
            entry = self.item_drafts.choose(offer_id, slot, self.inventory)
        except InventoryError:
            return
        self.trace_action("item_acquired", entry=entry, offer_id=offer_id)
        self.record_history(f"You kept {ITEM_CATALOGUE[entry.item_id].name}.")
        if self.item_drafts.pending_offer is not None:
            self.show_item_offer(self.item_drafts.pending_offer)
            self.update_inventory_button()
            return
        self.finish_item_offer()

    def skip_item_offer(self, offer_id):
        if not self.item_offer_is_current(offer_id):
            return
        try:
            self.item_drafts.skip(offer_id)
        except InventoryError:
            return
        self.trace_action("item_offer_skipped", offer_id=offer_id)
        self.record_history("You left the items behind.")
        self.finish_item_offer()

    def reroll_item_offer(self, offer_id, slot):
        if not self.item_offer_is_current(offer_id):
            return
        try:
            offer = self.item_drafts.reroll(offer_id, slot, self.inventory.snapshot())
        except InventoryError:
            return
        self.trace_action("item_offer_rerolled", offer=offer, slot=slot)
        self.record_history(f"You rerolled an item in the {offer.room_id}.")
        self.show_item_offer(offer)

    def finish_item_offer(self):
        continuation = self.pending_room_action
        self.pending_room_action = None
        restore_rooms = self._draft_room_selection_visible
        restore_log = self._draft_message_log_visible
        self._draft_room_selection_visible = False
        self._draft_message_log_visible = False
        self.retire_item_offer_page()
        self.update_inventory_button()
        if self.phase != "action" or self.game_over:
            return
        if restore_rooms:
            self.room_buttons_widget.show()
        if restore_log:
            self.message_log.show()
        self.apply_weapon_gating()
        self.position_weapon_panel()
        if continuation is not None:
            continuation()
        self.overlay_host.resume_unlocks()

    def can_use_items(self):
        return self.phase == "conversation" and not self.ai_busy and not self.game_over and self.player_alive

    def update_inventory_button(self):
        if not hasattr(self, "inventory_button"):
            return
        count = len(self.inventory.entries)
        self.inventory_button.setText(f"Inventory  {count}" if count else "Inventory")
        self.inventory_button.setEnabled(self.can_use_items())
        if (not self.can_use_items() and self.inventory_page is not None
                and self.overlay_host.current_page is self.inventory_page):
            self.overlay_host.close_page()

    def open_inventory(self):
        if not self.can_use_items():
            return
        current = self.overlay_host.current_page
        if ((current is not None and current is not self.inventory_page)
                or self.separate_surface_open()):
            return
        bodies = self.body_states()
        targets = [(body.body_id, self.display_label(body.body_id)) for body in bodies if body.alive and body.is_npc]
        options = {
            "living_body": [(body.body_id, self.display_label(body.body_id)) for body in bodies if body.alive],
            "dead_body": [(body.body_id, self.display_label(body.body_id)) for body in bodies if not body.alive],
            "room": [(room, room) for room in self.night_rooms if room not in self.old_crime_scenes],
        }
        unavailable = {item_id: "Not available in this development build yet."
                       for item_id in OPTIONAL_ITEM_NARRATION_PENDING}
        page = InventoryPage(self.inventory.entries, ITEM_CATALOGUE, targets, self.use_inventory_item,
                             can_use=self.can_use_items(), body_font=readable_body_font(),
                             title_font=self.speaker_font, target_options=options, unavailable=unavailable)
        self.inventory_page = page
        help_text = "Keep items between nights. Use them while the discussion is waiting for you."
        if current is not None:
            opened = self.overlay_host.replace_page(page, title="Inventory", help_text=help_text)
        else:
            opened = self.open_overlay_page(page, "Inventory", help_text)
        if not opened:
            self.inventory_page = None
            if current is not None:
                page.deleteLater()

    def item_use_context(self):
        return ItemUseContext(max(1, self.round_number), self.phase, self.ai_busy,
                              self.player_owner_id, self.body_id(self.player_name), self.body_states(),
                              available_rooms=tuple(room for room in self.night_rooms if room not in self.old_crime_scenes), required_player_reply=self.player_reply_required(),
                              memory_views=self.night_memory_views)

    def use_inventory_item(self, instance_id, target_body_id=None, *, message=None, room_id=None, activation_id=None):
        if not self.can_use_items():
            return False
        activation_id = activation_id or uuid4().hex
        context = self.item_activation_contexts.get(activation_id) or self.item_use_context()
        try:
            item_id = self.item_rules.activation_item_id(activation_id)
            if item_id is None:
                item_id = self.inventory.get(instance_id).item_id
            definition = ITEM_CATALOGUE[item_id]
            if definition.item_id in OPTIONAL_ITEM_NARRATION_PENDING:
                raise ItemRuleError("This item is not available in this development build yet.")
            if item_id == "soul" and self.item_rules.activation_item_id(activation_id) is None:
                self.ensure_identity_state()
                target_mind = self.identity_state.mind_for_body(target_body_id)
                if (self.identity_state.mind_name(self.player_owner_id) is None
                        or self.identity_state.mind_name(target_mind) is None):
                    raise ItemRuleError("This exchange requires both people's original identities")
            events = self.item_rules.use_item(instance_id, context, target_body_id,
                                             message=message, room_id=room_id, drafts=self.item_drafts,
                                             activation_id=activation_id)
        except (ItemRuleError, InventoryError) as error:
            self.secondary_output_area.setText(str(error))
            self.secondary_output_area.show()
            if self.inventory_page is not None:
                self.inventory_page.show_feedback(str(error))
            return False
        self.item_activation_contexts[activation_id] = context
        self.invalidate_prepared_turn("item_used", changed=True)
        self.request_serial += 1
        self.retry_action = None
        self.retry_button.hide()
        death_applied = self.apply_item_events(events)
        self.trace_action("inventory_item_used", events=events)
        if death_applied:
            if not self.player_alive:
                cause = next(event.death_decision.cause for event in events
                             if event.death_decision is not None
                             and event.death_decision.target_body_id == self.body_id(self.player_name)
                             and event.death_decision.outcome == "killed")
                score = 5 - sum(data["alive"] for data in self.characters.values())
                self.end_game_screen(score, death_cause=cause)
            else:
                self.check_victory()
        if self.game_over:
            return True
        self.restore_conversation_decision()
        if any(event.event_type == "early_vote_requested" for event in events):
            self.overlay_host.close_page()
            self.call_vote()
            return True
        if self.inventory_page is not None:
            self.open_inventory()
            messages = [f"Listening Glass: {(event.target_name or self.display_label(event.target_body_id))}, night {event.observed_day}\n{event.message}"
                        for event in events if event.event_type == "private_memory_revealed"]
            if messages and self.inventory_page is not None:
                self.inventory_page.show_feedback("\n\n".join(messages))
        self.maybe_prepare_next_turn()
        return True

    def personal_event_recipient(self, event):
        """Resolve personal ownership separately from the physical target body."""
        if event.recipient_mind_id is None:
            handle = self.label_for_body(event.target_body_id)
            return self.characters.get(handle), handle
        self.ensure_identity_state()
        if (not isinstance(event.recipient_mind_id, str)
                or event.recipient_mind_id not in self.mind_data):
            raise ItemRuleError("The captured personal recipient mind is unknown")
        return self.mind_data[event.recipient_mind_id], self.label_for_mind(event.recipient_mind_id)

    def apply_item_events(self, events):
        death_applied = False
        for event in events:
            personal_data, personal_handle = self.personal_event_recipient(event)
            if event.event_id in self.applied_item_events:
                continue
            if event.public_text:
                self.add_public_event(event.public_text, source="Observed event")
            if event.event_type == "soul_exchange_requested":
                self.apply_soul_exchange(event)
            if event.death_decision is not None:
                killed = self.apply_resolved_death(event.death_decision)
                death_applied = killed or death_applied
                if (killed and event.event_type == "immediate_death_resolved"
                        and event.owner_id == self.player_owner_id
                        and event.death_decision.target_body_id != self.body_id(self.player_name)):
                    self.queue_progress("player_kill")
                if event.event_type == "immediate_death_resolved":
                    if event.item_id == "lucky_coin" and self.player_alive:
                        self.coin_survived_this_run = True
                    decision = event.death_decision
                    label = self.perceived_name(decision.target_body_id)
                    self.record_history(
                        f"{ITEM_CATALOGUE[event.item_id].name}: {label}, {decision.outcome}.", display=True)
                    if killed:
                        self.item_deaths[decision.attempt_id] = ItemDeathRecord(
                            decision.attempt_id, decision.target_body_id, decision.day, decision.cause, None,
                            self.perceived_name(decision.target_body_id))
            self.applied_item_events.add(event.event_id)
            self.trace.record("item_effect", {"event": event})
            recipient = self.label_for_body(event.target_body_id)
            if personal_data is not None:
                if event.private_text:
                    self.remember_private(personal_data, personal_handle, "experience", event.private_text)
                if event.disposition_text:
                    self.remember_private(personal_data, personal_handle, "disposition", event.disposition_text)
            if event.event_type == "love_potion_applied" and personal_data is not None:
                data = personal_data
                history = data.get("love_history", LovePotionHistory())
                updated = history.apply(LoveDose(event.event_id, event.day, event.subject_body_id, event.giver_label))
                if updated is not history:
                    data["love_history"] = updated
                    self.invalidate_prepared_turn("current_feelings_changed", changed=True)
            if event.event_type == "npc_recollections_cleared":
                self.clear_npc_recollections()
            elif event.event_type == "banana_given":
                self.queue_progress("banana_given")
            elif event.event_type == "apparent_speech" and event.origin == "projected_voice":
                self.append_speech(recipient, event.message, origin=event.origin, perceived_speaker=event.target_name)
            elif event.event_type == "private_memory_revealed":
                self.record_history(f"Listening Glass: {event.target_name or self.display_actor(recipient)}, night {event.observed_day}\n{event.message}", display=True)
            elif event.event_type == "prepared_note_bound":
                self.morning_item_notes.append((event.event_id, event.target_name or self.perceived_name(event.target_body_id), event.message))
            elif event.event_type == "reserved_room_kill":
                self.queue_progress("reserved_room_kill")
            elif event.event_type == "extra_action_earned":
                self.queue_progress("marked_kill_completed")
            elif event.event_type == "vote_skipped":
                self.record_history("Presidential Decree: the next vote was skipped.", display=True)
            elif event.event_type == "execution_stayed":
                self.add_public_event(GUARDIAN_PUBLIC.format(target=event.target_name or self.perceived_name(event.target_body_id)))
            elif event.event_type == "guardian_intervened" and recipient == self.player_name:
                self.guardian_intervened_this_run = True
            elif event.event_type == "item_used":
                self.items_used += 1
                distinct_before = len(self.used_item_types)
                self.used_item_types.add(event.item_id)
                if distinct_before < 5 <= len(self.used_item_types):
                    self.queue_progress("five_item_types_used")
                item_name = ITEM_CATALOGUE[event.item_id].name
                target = f" on {event.target_name or self.display_actor(recipient)}" if recipient is not None and recipient != self.player_name else ""
                self.record_history(f"You used {item_name}{target}.")

        self.update_muffled_player_controls()
        return death_applied

    def apply_delayed_item_resolution(self, resolution, *, room_id=None, disclose=True):
        """Reconcile engine decisions without inventing ordinary attack witnesses."""
        killed_any = False
        for decision in resolution.decisions:
            killed = self.apply_resolved_death(decision)
            self.trace.record("item_death_resolved", {
                "attempt_id": decision.attempt_id, "target_body_id": decision.target_body_id,
                "cause": decision.cause.value, "outcome": decision.outcome,
                "is_new": decision.is_new, "applied": killed,
            })
            if not killed:
                continue
            killed_any = True
            self.item_deaths[decision.attempt_id] = ItemDeathRecord(
                decision.attempt_id, decision.target_body_id, decision.day, decision.cause, room_id,
                self.perceived_name(decision.target_body_id))
            label = self.perceived_name(decision.target_body_id)
            if disclose:
                self.record_history(f"{decision.cause.value}: {label} died.", display=True)
                self._disclosed_item_deaths.add(decision.attempt_id)
                self.observe_night_death(decision.target_body_id)
            if decision.target_body_id != self.body_id(self.player_name):
                self.queue_progress("player_kill")
        self.apply_item_events(resolution.events)
        return killed_any

    def resolve_action_curses(self):
        bodies = self.body_states()
        result = self.item_rules.resolve_due_curses(
            f"{self.run_id}:action:{self.round_number}", self.round_number, bodies,
            owners={self.body_id(self.player_name): self.player_owner_id})
        self.apply_delayed_item_resolution(result)
        return self.finish_item_death_outcomes(result.decisions)

    def resolve_room_entry_items(self, entry_id, room_id, body_ids, *, disclose=True):
        requested = set(body_ids)
        if not requested:
            return False
        entrants = tuple(body for body in self.body_states() if body.body_id in requested)
        if len(entrants) != len(requested):
            raise ItemRuleError("A room entry requires known body identifiers")
        owners = {body.body_id: self.player_owner_id for body in entrants
                  if body.body_id == self.body_id(self.player_name)}
        result = self.item_rules.resolve_entry(entry_id, room_id, entrants, self.round_number, owners=owners)
        if not result.decisions and not result.events:
            return False
        self.apply_delayed_item_resolution(result, room_id=room_id, disclose=disclose)
        return self.finish_item_death_outcomes(result.decisions)

    def finish_item_death_outcomes(self, decisions):
        if self.game_over:
            return True
        if not self.player_alive:
            cause = next(decision.cause for decision in decisions
                         if decision.target_body_id == self.body_id(self.player_name)
                         and decision.outcome == "killed")
            score = 5 - sum(data["alive"] for data in self.characters.values())
            self.end_game_screen(score, death_cause=cause)
            return True
        return self.check_victory()

    def publish_item_bodies(self):
        for record in self.item_deaths.values():
            if (record.day != self.round_number or record.room_id is None
                    or record.attempt_id in self.published_item_deaths
                    or record.body_id == self.body_id(self.player_name)):
                continue
            label = record.observed_name or self.label_for_body(record.body_id)
            self.add_public_event(
                f"In the morning the Detective reveals {label}'s body was found in the {record.room_id}.")
            self.published_item_deaths.add(record.attempt_id)

    def apply_resolved_death(self, decision):
        target = next((body for body in self.body_states()
                       if body.body_id == decision.target_body_id), None)
        if target is None or not decision.attempt_id or decision.outcome not in {"killed", "prevented", "already_dead"}:
            raise ItemRuleError("A resolved death requires a known body, attempt and outcome")
        self.apply_item_events(decision.events)
        if decision.attempt_id in self.applied_death_attempts:
            return False
        killed = decision.outcome == "killed" and target.alive
        if killed:
            if target.is_npc:
                self.characters[target.label]["alive"] = False
            else:
                self.player_alive = False
        self.applied_death_attempts.add(decision.attempt_id)
        self.invalidate_prepared_turn("death_resolved", changed=True)
        self.refresh_known_equipment()
        self.refresh_conversation_stage()
        return killed

    def resolve_death(self, label, attempt_id, *, cause=DeathCause.ATTACK, source_id=None, caused_by_player=False):
        target = next(body for body in self.body_states() if body.label == label)
        owner = self.player_owner_id if label == self.player_name else None
        decision = self.item_rules.attempt_death(
            attempt_id, target, max(1, self.round_number), owner, cause=cause, source_id=source_id,
            caused_by_owner_id=self.player_owner_id if caused_by_player else None,
            phase=self.phase if caused_by_player else None,
            room_id=next((room for room, occupants in self.room_assignments.items() if label in occupants), None))
        self.apply_resolved_death(decision)
        self.trace_action("death_resolved", attempt_id=attempt_id, target=target, outcome=decision.outcome,
                          is_new=decision.is_new)
        return decision

    def commit_attack(self, target, room, attacker=None, weapon_id=None):
        if not self.characters.get(target, {}).get("alive"):
            return False
        self.ensure_body_ids()
        attacker = attacker or self.player_name
        weapon_id = weapon_id or (self.selected_weapon or {}).get("id", "bare")
        attack = {"attack_id": uuid4().hex, "room": room, "weapon_id": weapon_id,
                  "attacker_label": attacker, "attacker_body_id": self.body_id(attacker),
                  "player_attack": attacker == self.player_name,
                  "target_label": target, "target_body_id": self.body_id(target),
                  "corpse_label": target, "corpse_body_id": self.body_id(target),
                  "attacker_name": self.perceived_actor(attacker), "target_name": self.perceived_actor(target),
                  "corpse_name": self.perceived_actor(target), "player_name": self.perceived_actor(self.player_name),
                  "observed_names": {self.body_id(name): self.perceived_actor(name)
                                     for name in (*self.room_assignments.get(room, ()), attacker, target)}}
        if weapon_id == "voodoo":
            if (not attack["player_attack"] or self.ai_busy or self.game_over
                    or self.phase != "action" or not self.player_alive):
                raise ItemRuleError("Voodoo requires the player's available night attack")
            resolution = self.voodoo_resolver.resolve(
                attack["attack_id"], self.identity_state, attack["attacker_body_id"],
                attack["target_body_id"], self.body_states(), self.round_number,
                guardians=(self.item_rules,))
            attack["voodoo_outcome"] = resolution.outcome
            if resolution.outcome == "mirrored":
                if resolution.identity_application_required:
                    self.identity_state = resolution.identity
                    self.invalidate_prepared_turn("appearance_changed", changed=True)
                    self.request_serial += 1
                    self.retry_action = None
                    self.required_reply_anchor = None
                    self.prepared_retry_snapshot = None
                    self.cancel_dictation()
                    self.retire_weapon_editors()
                    self.refresh_recipient_choices()
                    self.refresh_conversation_stage()
                decision = resolution.protection
                self.apply_resolved_death(decision)
            else:
                old_body = self.player_name
                self.exchange_player_mind(self.body_id(target), resolved_identity=resolution.identity)
                self.voodoo_current_identity = target
                self.room_assignments[room].remove(target)
                if old_body not in self.room_assignments[room]:
                    self.room_assignments[room].append(old_body)
                attack.update(corpse_label=old_body, corpse_body_id=self.body_id(old_body),
                              corpse_name=attack["attacker_name"])
                decision = self.resolve_death(old_body, attack["attack_id"], caused_by_player=True)
                if decision.killed:
                    self.record_history(f"You possessed the body of {attack['target_name']} in the {room} and left behind {attack['corpse_name']} as a corpse.")
        else:
            decision = self.resolve_death(target, attack["attack_id"], caused_by_player=True)
        attack["killed"] = decision.killed
        if not decision.killed:
            if weapon_id == "voodoo":
                self.night_attacks.append(attack)
                self.trace_action("voodoo_survived", room=room, attack=attack)
            return False
        self.night_attacks.append(attack)
        if weapon_id == "pen":
            self.pen_last_victim_name = attack["corpse_label"]
        self.victims_last_night.append(attack["corpse_label"])
        if room not in self.new_crime_scenes:
            self.new_crime_scenes.append(room)
        self.record_history(f"You killed {attack['corpse_name']} in the {room}.", display=True)
        self.queue_progress("player_kill")
        completed = [event_id for event_id, (body_id, due_day) in self.soul_former_bodies.items()
                     if attack["player_attack"] and self.phase == "action"
                     and attack["corpse_body_id"] == body_id and self.round_number == due_day]
        if completed:
            for event_id in completed:
                del self.soul_former_bodies[event_id]
            self.queue_progress("former_body_killed_following_night")
        self.trace_action("kill_resolved", victim=attack["corpse_label"], room=room, attack=attack)
        return True

    def show_development_trace_notice(self, message):
        self.append_output_text("[Development] " + message)

    def start_development_trace(self):
        if not self.trace.enabled or self.trace.run_id is not None:
            return
        root = os.path.dirname(resource_path("System Prompt.txt"))
        self.trace.start({"gameplay_run_id": self.run_id,
                          "source": source_fingerprint(root), "prompts": prompt_fingerprints(root),
                          "models": asdict(self.ai_runtime.config),
                          "settings": {"show_room_allocations": self.show_room_allocations},
                          "replay": "captured_inputs_not_deterministic_simulation"})
        self.trace_state("game_started")

    def trace_state(self, reason):
        if not self.trace.enabled or self.trace.run_id is None:
            return
        fields = ("identity_state", "body_weapons", "mind_weapons", "player_name", "characters", "rooms", "room_assignments", "round_number", "phase",
                  "game_over", "show_room_allocations", "old_crime_scenes", "new_crime_scenes",
                  "rooms_visited", "night_rooms", "selected_weapon", "weapon_pen_backfire", "pending_pen_note_text",
                  "black_magic_controller", "black_magic_target", "knife_extra_target",
                  "knife_used_bloodlust", "c4_target_room", "c4_cooldown_nights",
                  "player_sleep_room", "voodoo_current_identity", "stopwatch_selected",
                  "victims_last_night", "public_events", "public_dialogue", "votes",
                  "_c4_exploded_last_night", "_c4_last_room", "_override_murder_room",
                  "pen_last_victim_name", "had_to_change_rooms", "awaiting_player_vote", "voting_resolved",
                  "ai_busy", "request_serial")
        state = {name: getattr(self, name, None) for name in fields}
        state["expected_voters"] = sorted(getattr(self, "expected_voters", ()))
        state["last_addressee"] = self.public_dialogue[-1].addressee if self.public_dialogue else None
        state["npc_items"] = self.npc_items.trace_state()
        state["player_items"] = {
            "run_id": self.run_id, "owner_id": self.player_owner_id,
            "body_ids": dict(self.body_ids), "player_alive": self.player_alive,
            "inventory": self.inventory.entries, "run_pool": self.run_item_pool,
            "run_weapons": self.run_weapons, "protection_armed": self.item_rules.protection_armed,
            "ballot_bonus": self.item_rules.pending_ballot_bonus,
            "muffles": self.item_rules.muffles, "affinities": self.item_rules.affinities,
            "offer": self.item_drafts.pending_offer if self.item_drafts else None,
            "seen_rooms": sorted(self.item_drafts.seen_rooms) if self.item_drafts else [],
            "rerolls_remaining": self.item_drafts.rerolls_remaining if self.item_drafts else 0,
            "attacks": self.night_attacks, "witness_body_ids": sorted(self.witness_body_ids),
            "item_deaths": self.item_deaths,
            "published_item_deaths": sorted(self.published_item_deaths),
            "progress_sequence": self.progress_sequence, "pending_progress": self.pending_progress,
            "run_player_kills": self.run_player_kills,
            "vote_snapshot": self.vote_snapshot,
            "vote_weights": dict(self.vote_snapshot.weights) if self.vote_snapshot else {},
        }
        active = self.turn_preparation.active
        state["conversation_control"] = {
            "revision": self.conversation_revision,
            "direct_discussion": self.direct_replies.discussion_id,
            "direct_body_id": self.direct_replies.body_id,
            "direct_count": self.direct_replies.count,
            "preparing_ticket": active.identifier if active is not None else None,
            "preparation_cancelled": active.cancelled.is_set() if active is not None else False,
            "awaiting_ticket": self.awaiting_preparation,
            "cached_ticket": self.turn_preparation.ready.ticket_id if self.turn_preparation.ready else None,
        }
        self.trace.snapshot(state, reason=reason)

    def trace_action(self, action, **details):
        self.trace.record("action_applied", {"action": action, "round": self.round_number,
                                            "phase": self.phase, **details})
        self.trace_state(action)

    def trace_job_options(self, task, actor=None, parent_job_id=None):
        if not self.trace.enabled or self.trace.run_id is None:
            return {}
        return {"trace_task": task, "trace_actor": actor, "trace_parent_job_id": parent_job_id}

    def remember(self, character, kind, text):
        self.remember_private(self.characters[character], character, kind, text)

    def remember_private(self, data, character, kind, text):
        self.invalidate_prepared_turn("memory_changed", changed=True)
        section = {"experience": "experiences", "disposition": "dispositions"}[kind]
        data[section].append(MemoryRecord(self.round_number, kind, text, "self"))
        self.trace.record("memory_added", {"character": character, "kind": kind,
                                          "round": self.round_number, "text": text})

    def make_display_record(self, text, *, kind="event", speaker=None, speaker_id=None):
        self.display_sequence += 1
        return DisplayRecord(f"{self.run_id}:{self.display_sequence}", kind, text,
                             speaker=speaker, speaker_id=speaker_id,
                             round_number=self.round_number, phase=self.phase)

    def record_history(self, text, *, kind="event", speaker=None, speaker_id=None, display=False):
        """Record player-visible history separately from NPC memory and live status."""
        record = self.make_display_record(text, kind=kind, speaker=speaker, speaker_id=speaker_id)
        self.history_records.append(record)
        self.history_record_added.emit(record)
        if display:
            self.message_log.append_record(record)
        return record

    def append_output_text(self, text):
        """Display literal text without making temporary UI status permanent history."""
        self.message_log.append_record(self.make_display_record(text))

    def speaker_display_format(self, record):
        result = QTextCharFormat()
        handle = self.label_for_body(record.speaker_id) if record.speaker_id is not None else None
        if (handle is not None and handle != self.player_name
                and self.characters.get(handle, {}).get("alive", False)):
            result.setAnchor(True)
            result.setAnchorHref("npc:" + record.speaker_id)
            result.setForeground(Qt.lightGray)
            result.setFontUnderline(False)
        return result

    def overlay_page_closed(self, title):
        if title == "Inventory":
            self.inventory_page = None
        elif title == "Transcript":
            self.transcript_page = None
        elif title == "How to Play":
            self.help_page = None
        elif title == "Menu":
            self._esc_overlay = None
            self._esc_sound_btn = None

    def add_public_event(self, text, source="Detective", display=True):
        self.invalidate_prepared_turn("public_event", changed=True)
        self.public_events.append(MemoryRecord(self.round_number, "public_event", text, source))
        self.trace.record("public_event", {"source": source, "round": self.round_number, "text": text})
        record = self.record_history(text, speaker="Detective" if source in ("Detective", "Photograph") else None)
        self.public_event_display_records.append(record)
        if display and self.phase in ("conversation", "voting"):
            self.message_log.append_record(record)

    def living_actor_handles(self):
        alive = {char for char, data in self.characters.items() if data["alive"]}
        if self.player_name and self.player_alive:
            alive.add(self.player_name)
        return tuple(sorted(alive))

    def ai_reference_routes(self):
        return {self.display_actor(handle): handle for handle in self.living_actor_handles()}

    @staticmethod
    def routed_ai_proposal(proposal, reference_routes):
        addressee = reference_routes.get(proposal.addressee) if proposal.addressee is not None else None
        return SpeechProposal(addressee, proposal.speech)

    def ai_snapshot_is_current(self, snapshot):
        try:
            return self.group_turn_snapshot() == snapshot
        except (ItemRuleError, ValueError, KeyError):
            # An inconsistent identity/context cannot validate an in-flight result.
            return False

    def public_state(self):
        self.ensure_identity_state()
        handles = self.living_actor_handles()
        copresent = self.phase in {"conversation", "voting"}
        visible = sorted((self.display_actor(handle) if copresent else self.perceived_actor(handle),
                          self.perceived_actor(handle)) for handle in handles)
        alive = tuple(reference for reference, _name in visible)
        people = tuple(PersonPresent(name, reference) for reference, name in visible) if copresent else ()
        dead = tuple(self.perceived_actor(char) for char, data in self.characters.items()
                     if not data["alive"] and char != self.player_name)
        return PublicState(self.round_number, alive, dead, self.night_rooms, people)

    def recalled_public_events(self):
        return tuple(self.public_events[self.npc_recall_public_event_start:])

    def recalled_routing_dialogue(self):
        return tuple(self.public_dialogue[self.npc_recall_dialogue_start:])

    def recalled_dialogue(self):
        return tuple(self.dialogue_perceptions.get(id(line), (line, line))[1]
                     for line in self.recalled_routing_dialogue())

    def clear_npc_recollections(self):
        self.ensure_identity_state()
        for mind_id, data in self.mind_data.items():
            if mind_id == self.player_owner_id:
                continue
            data["recall_experience_start"] = len(data["experiences"])
            data["recall_disposition_start"] = len(data["dispositions"])
            history = data.get("love_history")
            if history is not None:
                data["love_history"] = replace(
                    history, experiences=history.experiences.forget_recollections(self.round_number))
        self.npc_recall_public_event_start = len(self.public_events)
        self.npc_recall_dialogue_start = len(self.public_dialogue)
        self.night_memory_views = ()
        self.pending_direct_reply = None
        self.invalidate_prepared_turn("npc_recollections_cleared", changed=True)

    def build_character_context(self, character):
        self.ensure_identity_state()
        data = self.characters[character]
        private = data.get("love_history", LovePotionHistory()).experiences.project()
        experiences = tuple(data["experiences"][data.get("recall_experience_start", 0):])
        dispositions = tuple(data["dispositions"][data.get("recall_disposition_start", 0):])
        return CharacterContext(character, tuple(data["profile"]), self.public_state(),
                                (*experiences, *private.experiences), dispositions,
                                self.recalled_public_events(), self.recalled_dialogue(), private.current_feelings,
                                SelfIdentity(self.identity_state.mind_name(self.mind_id(character)),
                                             self.perceived_actor(character), self.display_actor(character)))

    def advance_love_histories(self, *, discussion):
        changed = False
        for data in self.characters.values():
            history = data.get("love_history")
            if history is None:
                continue
            updated = (history.begin_discussion(self.round_number, alive=data["alive"]) if discussion
                       else history.begin_night(self.round_number))
            if updated is not history:
                data["love_history"] = updated
                changed = True
        if changed:
            self.invalidate_prepared_turn("private_experiences_changed", changed=True)

    def append_speech(self, speaker, speech, addressee=None, *, origin="ordinary", direct_reply=False,
                      perceived_speaker=None):
        if speaker == self.player_name and origin == "ordinary" and self.player_is_muffled():
            if (self.phase != "conversation" or self.game_over or self.ai_busy
                    or not self.player_reply_required()):
                return False
            speech = muffled_sound(self.player_name, self.muffled_player_tone.currentData())
            addressee = None
        if self.phase == "conversation":
            self.direct_replies.begin_discussion(self.round_number)
        ordinary_npc = (origin == "ordinary" and speaker != self.player_name
                        and self.characters.get(speaker, {}).get("alive", False))
        if ordinary_npc and direct_reply and not self.direct_replies.can_direct(self.body_id(speaker)):
            return False
        self.pending_direct_reply = None
        self.invalidate_prepared_turn("speech_committed", changed=True)
        self.retry_action = None
        self.retry_button.hide()
        self.request_serial += 1
        canonical_speaker = self.perceived_actor(speaker) if perceived_speaker is None else perceived_speaker
        display_speaker = (self.display_actor(speaker) if perceived_speaker is None
                           or self.perceived_actor(speaker) == perceived_speaker else perceived_speaker)
        line = DialogueLine(self.round_number, speaker, speech, addressee)
        self.public_dialogue.append(line)
        perception = DialogueLine(self.round_number, canonical_speaker, speech,
                                  self.perceived_actor(addressee))
        self.dialogue_perceptions[id(line)] = (line, perception)
        self.last_speech_origin = (line, origin)
        if origin == "ordinary":
            self.required_reply_anchor = ((line, self.body_id(self.player_name))
                                          if addressee == self.player_name and speaker != self.player_name else None)
        self.trace.record("speech_committed", {"round": self.round_number, "speaker": speaker,
                                              "speech": speech, "addressee": addressee,
                                              "origin": origin, "direct_reply": direct_reply},
                          scope=self.trace_callback_scope)
        message = f"{canonical_speaker}: {speech}"
        self.current_conversation.append(message)
        self.most_recent_messageS.append(message)
        speaker_id = self.body_id(speaker) if speaker in self.characters or speaker == self.player_name else None
        self.record_history(speech, kind="speech", speaker=display_speaker,
                            speaker_id=speaker_id, display=True)
        self.conversation_stage.set_active_speaker(speaker_id)
        if ordinary_npc:
            self.direct_replies.committed(self.body_id(speaker), direct=direct_reply)
        self.refresh_recipient_choices()
        if speaker == "Detective" or speaker in self.characters:
            self.play_sound_effect(f"sounds/voices/{speaker}.mp3")
        return True

    def start_ai_request(self, operation, on_success, status, *, trace_task=None,
                         trace_actor=None, trace_parent_job_id=None, trace_scope=None, context_guard=None):
        if self.game_over or self.ai_busy or self.player_reply_required():
            return
        if context_guard is not None and not context_guard():
            self.retry_action = None
            self.retry_button.hide()
            return
        self.invalidate_prepared_turn("foreground_request")
        self.request_serial += 1
        token, phase = self.request_serial, self.phase
        scope = trace_scope or self.trace.new_job(trace_task or "operation",
            parent_job_id=trace_parent_job_id,
            metadata={"actor": trace_actor, "round": self.round_number, "phase": phase})
        if scope is not None:
            scope.record("job_started", {"request_serial": token, "round": self.round_number,
                                         "phase": phase, "status": status})
        self.ai_busy = True
        self.update_inventory_button()
        self.retry_action = None
        self.retry_button.hide()
        self.pass_button.setEnabled(False)
        self.interject_button.setEnabled(False)
        self.vote_button.setEnabled(False)
        self.interject_input.setEnabled(False)
        self.say_button.setEnabled(False)
        self.secondary_output_area.setText("...")
        self.secondary_output_area.show()

        def current():
            return not self.game_over and self.phase == phase and self.request_serial == token

        def ready(result):
            if scope is not None:
                scope.record("job_result", {"result": result})
            if not current():
                if scope is not None:
                    scope.record("job_ignored", {"reason": "stale_result", "current_phase": self.phase,
                                                 "current_serial": self.request_serial})
                return
            if context_guard is not None and not context_guard():
                self.ai_busy = False
                self.retry_action = None
                self.retry_button.hide()
                self.restore_conversation_decision()
                return
            self.ai_busy = False
            self.update_inventory_button()
            self.interject_input.setEnabled(True)
            self.say_button.setEnabled(True)
            self.secondary_output_area.clear()
            self.last_completed_trace_job_id = scope.job_id if scope is not None else None
            previous_scope = self.trace_callback_scope
            self.trace_callback_scope = scope
            try:
                on_success(result)
            finally:
                self.trace_callback_scope = previous_scope
            self.update_muffled_player_controls()
            if scope is not None:
                scope.record("job_applied", {"round": self.round_number, "phase": self.phase})

        def failed(message):
            if scope is not None:
                scope.record("job_failed", {"error_type": "request_failed"})
            if not current():
                if scope is not None:
                    scope.record("job_ignored", {"reason": "stale_failure", "current_phase": self.phase,
                                                 "current_serial": self.request_serial})
                return
            if context_guard is not None and not context_guard():
                self.ai_busy = False
                self.retry_action = None
                self.retry_button.hide()
                self.restore_conversation_decision()
                return
            self.ai_busy = False
            self.update_inventory_button()
            self.interject_input.setEnabled(True)
            self.say_button.setEnabled(True)
            self.secondary_output_area.setText(message)
            self.retry_action = lambda: self.start_ai_request(
                operation, on_success, status, trace_task=trace_task, trace_actor=trace_actor,
                trace_parent_job_id=trace_parent_job_id,
                trace_scope=scope.retry() if scope is not None else None, context_guard=context_guard)
            self.retry_button.show()
            if phase == "conversation":
                self.pass_button.setEnabled(not self.player_reply_required())
                self.interject_button.setEnabled(True)
                self.vote_button.setEnabled(self.can_call_vote())
            self.update_muffled_player_controls()

        worker = AIWorker(lambda: operation(scope) if trace_task is not None else operation())
        worker.result_ready.connect(ready)
        worker.failed.connect(failed)
        self.start_worker(worker)

    def start_worker(self, worker):
        worker.finished.connect(lambda: self.release_ai_worker(worker))
        self.active_workers.append(worker)
        app = QApplication.instance()
        if not hasattr(app, "_ai_workers"):
            app._ai_workers = set()
        app._ai_workers.add(worker)
        worker.start()

    def release_ai_worker(self, worker):
        self.active_workers = [active for active in self.active_workers if active is not worker]
        app = QApplication.instance()
        app._ai_workers.discard(worker)
        worker.deleteLater()
        if getattr(app, "_quit_when_idle", False) and not app._ai_workers:
            app.quit()

    def retry_ai_request(self):
        retry = self.retry_action
        if retry is not None and not self.game_over and not self.ai_busy and not self.player_reply_required():
            retry()

    def handle_ai_votes(self, votes):
        if self.phase != "voting" or self.game_over:
            return
        for character, vote in votes.items():
            self.collect_ai_vote(character, vote)

    def closeEvent(self, event):
        restarting = self.phase == "restarting"
        if not restarting:
            self.save_player_name_preference()
        self.game_over = True
        self.cancel_item_offer()
        self.dictation_bindings.close()
        self.overlay_host.cancel_owner()
        self.invalidate_prepared_turn("closed", changed=True)
        self.cancel_dictation()
        self.dictation_recorder.close()
        self.request_serial += 1
        if not restarting:
            self.phase = "closed"
            self.trace_action("window_closed")
            self.trace.end("closed")
            app = QApplication.instance()
            if getattr(app, "_ai_workers", None) and app.quitOnLastWindowClosed():
                app.setQuitOnLastWindowClosed(False)
                app._quit_when_idle = True
        super().closeEvent(event)

    def init_ui(self):
        self.setWindowTitle("AlibAi")

        font_id = QFontDatabase.addApplicationFont(resource_path("fonts/PressStart2P-Regular.ttf"))
        font_families = QFontDatabase.applicationFontFamilies(font_id)
        font_family = font_families[0] if font_families else "Sans-Serif"
        self.pixel_font_family = font_family
        body_font = readable_body_font()
        body_family = body_font.family()

        # Output area (TextEdit) to display game output, hidden initially
        self.output_area = DialogueView(self)
        self.output_area.speaker_chosen.connect(self.choose_speaker_recipient)
        self.output_area.setReadOnly(True)
        self.output_area.document().setDefaultFont(body_font)
        self.speaker_font = QFont(font_family)
        self.speaker_font.setPixelSize(12)
        self.message_log = MessageLog(self.output_area, body_font=body_font,
                                      speaker_font=self.speaker_font, name_format=self.speaker_display_format)
        self.message_log.hide()
        self.conversation_stage = ConversationStage(self, name_font=self.speaker_font)
        self.conversation_stage.speaker_chosen.connect(self.choose_speaker_recipient)
        self.conversation_stage.hide()
        self.overlay_host = OverlayHost(self, body_font=body_font, title_font=self.speaker_font,
                                        unlock_ready=self.unlock_celebrations_ready)
        self.overlay_host.setStyleSheet("QDialog#gameOverlayHost { background: #090909; border: 1px solid #aaa; } "
                                       "QLabel { color: #E0E0E0; } "
                                       "QToolButton { color: white; background: #333; border: 1px solid #777; padding: 8px; } "
                                       "QToolButton:hover, QToolButton:focus { background: #444; border-color: white; }")
        self.overlay_host.page_closed.connect(self.overlay_page_closed)
        self.overlay_host.unlock_failed.connect(self.show_unlock_error)
        self.overlay_host.installEventFilter(self)
        for control in (self.overlay_host.close_button, self.overlay_host.help_button):
            control.setFont(self.speaker_font)
            control.setStyleSheet(
                f'QToolButton {{ font-family: "{font_family}"; font-size: 10px; color: #ddd; '
                'background: transparent; border: 1px solid #555; padding: 10px; } '
                'QToolButton:hover, QToolButton:focus { color: white; border-color: white; }')



        screen = QGuiApplication.primaryScreen()  # Get the primary screen
        screen_size = screen.size()  # Get screen size
        screen_width = screen_size.width()

        
        # Title of the game
        self.title_label = QLabel("AlibAi", self)
        self.title_label.setAlignment(Qt.AlignCenter)  # Center the title text

        # Input field for player's name
        self.player_name_label = QLabel(self)
        self.player_name_label.setText("Enter your name:")
        self.player_name_label.setFixedHeight(40)
        self.player_name_label.setAlignment(Qt.AlignCenter)
        self.player_name_label.setStyleSheet("font-size: 18px; color: #DCDCDC;")

        self.player_name_input = QLineEdit(self)
        self.player_name_input.setFixedHeight(40)  # Increase the height of the input field
        self.player_name_input.setAlignment(Qt.AlignCenter)
        self.player_name_input.setStyleSheet("""
            QLineEdit {
                font-size: 20px;
                padding: 5px;
                text-align: center;
                color: #DCDCDC;
                border: none;
            }
        """)
        self.player_name_input.setPlaceholderText("Type here...")
        name_palette = self.player_name_input.palette()
        name_palette.setColor(QPalette.PlaceholderText, QColor("#888888"))
        self.player_name_input.setPalette(name_palette)
        self.player_name_input.setText(self.entered_player_name)
        self.player_name_input.textChanged.connect(self.remember_entered_player_name)
        self.player_name_input.editingFinished.connect(self.save_player_name_preference)

        # Reduce spacing between "Enter your name" label and the input field
        self.player_name_layout = QVBoxLayout()
        self.player_name_layout.addWidget(self.player_name_label)
        self.player_name_layout.addWidget(self.player_name_input)
        self.player_name_layout.setSpacing(5)  # Set spacing between label and input to 5px

        start_buttons_font_size = max(int(50*(screen_width/2560)), 37)

        # Toggle button for game difficulty (Normal/Super Hard)
        self.difficulty_button = QPushButton("Mode: Normal  >", self)
        self.difficulty_button.setAccessibleName("Change difficulty")
        self.difficulty_button.clicked.connect(self.toggle_mode)

        # Button to start the game
        self.start_button = QPushButton('Start Game!', self)
        self.start_button.clicked.connect(self.start_game)

        # Keep the root room widget as the inline reward insertion boundary.
        self.room_buttons_widget = QWidget()
        outer_room_layout = QHBoxLayout(self.room_buttons_widget)
        outer_room_layout.setContentsMargins(0, 0, 0, 0)
        self.house_row = QWidget()
        outer_room_layout.addStretch(1)
        outer_room_layout.addWidget(self.house_row)
        outer_room_layout.addStretch(1)
        room_layout = QHBoxLayout(self.house_row)
        room_layout.setContentsMargins(0, 0, 0, 0)
        room_layout.setSpacing(16)
        self.house_view = HouseView(resource_path("House_images/house-cutaway-v1.png"),
                                    body_font=body_font, heading_font=self.speaker_font,
                                    external_info=True)
        self.house_view.setMinimumWidth(1)
        room_layout.addWidget(self.house_view, 1)
        self.room_buttons = {label: self.house_view.buttons[room_id]
                             for room_id, label in ROOM_LABELS.items()}
        self.house_view.room_selected.connect(lambda room_id: self.choose_room(ROOM_LABELS[room_id]))
        self.house_sidebar = QScrollArea()
        self.house_sidebar.setFixedWidth(280)
        self.house_sidebar.setFrameShape(QFrame.NoFrame)
        self.house_sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.house_sidebar.setWidgetResizable(True)
        self.house_sidebar_content = QWidget()
        self.house_sidebar_layout = QVBoxLayout(self.house_sidebar_content)
        self.house_sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self.house_sidebar_layout.setSpacing(16)
        self.arrival_controls = QWidget()
        arrival_layout = QVBoxLayout(self.arrival_controls)
        arrival_layout.setContentsMargins(0, 0, 0, 0)
        arrival_layout.setSpacing(12)
        self.arrival_clock = QLabel("19:45")
        self.arrival_clock.setObjectName("arrivalClock")
        self.arrival_clock.setAccessibleName("Night arrival time")
        InventoryPage._font(self.arrival_clock, self.speaker_font)
        arrival_layout.addWidget(self.arrival_clock)
        self.loiter_button = QPushButton("Loiter outside")
        self.loiter_button.setObjectName("loiterButton")
        self.loiter_button.setAccessibleName("Wait outside for the next guest")
        self.loiter_button.setFocusPolicy(Qt.StrongFocus)
        InventoryPage._font(self.loiter_button, body_font)
        self.loiter_button.setMinimumHeight(44)
        arrival_layout.addWidget(self.loiter_button)
        self.arrival_status = QLabel()
        self.arrival_status.setObjectName("arrivalStatus")
        self.arrival_status.setWordWrap(True)
        InventoryPage._font(self.arrival_status, body_font)
        arrival_layout.addWidget(self.arrival_status)
        self.house_sidebar_layout.addWidget(self.arrival_controls)
        self.arrival_clock.hide()
        self.loiter_button.hide()
        self.arrival_status.hide()
        self.house_sidebar_layout.addWidget(self.house_view.info_panel)
        self.house_sidebar_layout.addStretch(1)
        self.house_sidebar.setWidget(self.house_sidebar_content)
        room_layout.addWidget(self.house_sidebar)

        self.secondary_output_area = QLabel(self)
        self.secondary_output_area.setAlignment(Qt.AlignCenter)
        self.secondary_output_area.setStyleSheet(f'color: grey; font-family: "{body_family}"; font-size: 18px;')
        self.secondary_output_area.hide()  # Initially hidden


        button_width = int(screen_width / 3)
        button_height = int(100 * (screen_width/2560))
        # Conversation phase buttons
        self.pass_button = QPushButton('Pass')
        self.pass_button.setMinimumHeight(48)
        self.pass_button.clicked.connect(self.pass_conversation)
        self.interject_button = QPushButton('Interject')
        self.interject_button.clicked.connect(self.interject_conversation)
        self.vote_button = QPushButton('Call Vote')
        self.vote_button.clicked.connect(self.call_vote)
        self.vote_button.setEnabled(False)  # Initially disabled
        self.vote_button.setToolTip("Available after the group has had time to discuss the night.")

        conversation_button_font_size = max(int(32 *(screen_width/2560)), 18)
        button_style = f"""
            QPushButton {{
                font-size: {conversation_button_font_size}px;
                border: none; background: transparent; padding: 12px;
            }}
            QPushButton:disabled {{
                color: #777;
            }}
            QPushButton:enabled:hover {{
                        color: #808080;
                    }}
        """
        self.pass_button.setStyleSheet(button_style)
        self.interject_button.setStyleSheet(button_style)
        self.vote_button.setStyleSheet(button_style)

        # Layouts
        self.conversation_buttons_layout = QHBoxLayout()
        for button in (self.pass_button, self.interject_button, self.vote_button):
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.conversation_buttons_layout.addWidget(button, 1)

        # Input field for interjecting during conversation
        self.interject_input = QLineEdit(self)
        self.interject_input.setFont(body_font)
        self.interject_input.setPlaceholderText("Type here...")
        self.interject_input.setAlignment(Qt.AlignLeft)
        self.interject_input.setMinimumHeight(48)
        self.interject_input.setStyleSheet(f"""
            QLineEdit {{
                font-family: "{body_family}";
                font-size: 18px;
                padding: 5px;
                text-align: center;
                color: #DCDCDC;
                border: none; border-bottom: 1px solid #555;
            }}
        """)
        self.interject_input.hide()  # Hidden until needed
        self.interject_input.returnPressed.connect(self.send_interjection)
        self.interject_input.textEdited.connect(lambda _text: self.invalidate_prepared_turn("draft_edited"))
        self.say_button = QPushButton('Say')
        self.say_button.setFixedWidth(112)
        self.say_button.setStyleSheet(f"""
            QPushButton {{
                font-size: {conversation_button_font_size}px;
                border: 1px solid #777; background: transparent; padding: 10px;
            }}
            QPushButton:hover {{
                color: #808080;
            }}
        """)
        self.say_button.clicked.connect(self.send_interjection)
        self.say_button.hide()  # Hidden until needed

        self.muffled_player_controls = QWidget()
        muffled_layout = QHBoxLayout(self.muffled_player_controls)
        muffled_layout.setContentsMargins(0, 0, 0, 0)
        muffled_label = QLabel("You can only make muffled sounds. Choose your tone:")
        self.muffled_player_tone = QComboBox()
        for tone, label in zip(TONES, TONE_LABELS):
            self.muffled_player_tone.addItem(label.capitalize(), tone)
        self.muffled_player_tone.setCurrentIndex(2)
        for widget in (muffled_label, self.muffled_player_tone):
            widget.setFont(body_font)
            widget.setStyleSheet(f'font-family: "{body_family}"; font-size: 18px; padding: 5px;')
            muffled_layout.addWidget(widget)
        self.muffled_player_controls.hide()

        self.recipient_controls = QWidget()
        recipient_layout = QHBoxLayout(self.recipient_controls)
        recipient_layout.setContentsMargins(0, 0, 0, 0)
        recipient_label = QLabel("Address")
        self.recipient_selector = GameComboBox()
        self.recipient_selector.addItem("Group", None)
        self.recipient_selector.currentIndexChanged.connect(self.update_recipient_controls)
        for widget in (recipient_label, self.recipient_selector):
            widget.setFont(body_font)
            widget.setStyleSheet(f'font-family: "{body_family}"; font-size: 18px; padding: 5px;')
            recipient_layout.addWidget(widget)
        recipient_label.setFont(self.speaker_font)
        recipient_label.setStyleSheet('font-size: 10px; color: #999; padding: 4px;')
        self.recipient_selector.setMinimumWidth(160)
        self.recipient_selector.setStyleSheet(
            f'QComboBox {{ font-family: "{body_family}"; font-size: 18px; color: #eee; '
            'background: #111; border: 1px solid #555; padding: 8px 30px 8px 12px; } '
            'QComboBox:hover, QComboBox:focus { border-color: #ddd; } '
            'QComboBox::drop-down { width: 26px; border-left: 1px solid #555; } '
            'QComboBox QAbstractItemView { background: #111; color: #eee; '
            'selection-background-color: #333; padding: 6px; }')
        self.recipient_controls.hide()
        self.recipient_hint = QLabel()
        self.recipient_hint.setTextFormat(Qt.PlainText)
        self.recipient_hint.setWordWrap(True)
        self.recipient_hint.setStyleSheet(f'font-family: "{body_family}"; font-size: 16px; color: #B8B8B8;')
        self.recipient_hint.hide()

        self.dictation_controls = QWidget()
        dictation_layout = QHBoxLayout(self.dictation_controls)
        dictation_layout.setContentsMargins(4, 4, 4, 4)
        self.dictation_button = QPushButton()
        self.dictation_button.setCheckable(True)
        self.dictation_button.setFixedSize(64, 64)
        self.dictation_button.setIconSize(QSize(48, 48))
        self.dictation_icons = {}
        for recording, state in ((False, "idle"), (True, "recording")):
            artwork = QPixmap(resource_path(f"icons/ui/microphone-{state}.png"))
            if not artwork.isNull():
                bounds = QRegion(artwork.mask()).boundingRect()
                if not bounds.isEmpty():
                    artwork = artwork.copy(bounds)
            self.dictation_icons[recording] = QIcon(artwork.scaled(
                48, 48, Qt.KeepAspectRatio, Qt.FastTransformation))
        self.dictation_button.clicked.connect(self.toggle_dictation)
        self.dictation_cancel_button = QPushButton("Cancel")
        self.dictation_cancel_button.clicked.connect(self.cancel_dictation)
        for button in (self.dictation_button, self.dictation_cancel_button):
            button.setFont(body_font)
            button.setStyleSheet(f'QPushButton {{ font-family: "{body_family}"; font-size: 18px; padding: 6px; }} QPushButton:disabled {{ color: #777; }}')
            dictation_layout.addWidget(button)
        self.dictation_button.setStyleSheet(
            f'QPushButton {{ font-family: "{body_family}"; font-size: 18px; padding: 4px; '
            'background: transparent; border: none; } '
            'QPushButton:hover, QPushButton:focus { background: transparent; } '
            'QPushButton:checked { background: transparent; } '
            'QPushButton:disabled { color: #777; }')
        self.dictation_glow = QGraphicsDropShadowEffect(self.dictation_button)
        self.dictation_glow.setOffset(0, 0)
        self.dictation_glow.setBlurRadius(12)
        self.dictation_glow.setColor(QColor(226, 183, 108, 110))
        self.dictation_glow.setEnabled(False)
        self.dictation_button.setGraphicsEffect(self.dictation_glow)
        self.dictation_controls.hide()
        self.dictation_cancel_button.hide()
        self.dictation_status = QLabel()
        self.dictation_status.setTextFormat(Qt.PlainText)
        self.dictation_status.setWordWrap(True)
        self.dictation_status.setFont(body_font)
        self.dictation_status.setStyleSheet(f'font-family: "{body_family}"; font-size: 18px; color: #B8B8B8;')
        self.dictation_status.hide()
        self.dictation_bindings = DictationBindings(
            self, self.toggle_dictation, binding=self.preferences.dictation_binding(),
            can_trigger=self.can_toggle_dictation, conflict_reason=self.dictation_binding_conflict)
        self.dictation_bindings.activation_failed.connect(self.show_dictation_binding_failure)
        self.dictation_binding_badge = BindingBadge(self.dictation_bindings, font=body_font)
        dictation_layout.addWidget(self.dictation_binding_badge)
        self.update_dictation_controls()
        self.interject_input.installEventFilter(self)

        # Weapon Picker
        self.weapon_grid = QGridLayout()
        self.weapon_grid.setSpacing(16)
        self.weapon_picker_widget = QWidget()
        self.weapon_picker_widget.setLayout(self.weapon_grid)
        self.weapon_scroll = QScrollArea()
        self.weapon_scroll.setWidgetResizable(True)
        self.weapon_scroll.setFrameShape(QFrame.NoFrame)
        self.weapon_scroll.setWidget(self.weapon_picker_widget)
        self.weapon_scroll.hide()
        self.npc_equipment_label = QLabel()
        self.npc_equipment_label.setTextFormat(Qt.PlainText)
        self.npc_equipment_label.setWordWrap(True)
        self.npc_equipment_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.npc_equipment_label.setFont(body_font)
        self.npc_equipment_label.setStyleSheet(
            f'font-family: "{body_family}"; font-size: 18px; color: #E0E0E0;')
        self.house_sidebar_layout.insertWidget(1, self.npc_equipment_label)
        self.npc_equipment_label.hide()

        # Main Layout
        self.start_layout = QVBoxLayout()
        self.start_layout.addStretch(5)
        self.start_layout.addWidget(self.title_label)  # Add the title at the top
        self.start_layout.addSpacing(24)
        self.start_layout.addLayout(self.player_name_layout)
        self.start_layout.addStretch(2)
        self.start_layout.addWidget(self.difficulty_button)
        self.start_layout.addWidget(self.start_button)
        self.start_layout.addStretch(1)
        self.start_layout.setContentsMargins(24, 16, 24, 16)
        self.start_layout.setSpacing(16)
        self.play_content = QWidget()
        self.play_content.setMaximumWidth(1000)
        self.play_content.setLayout(self.start_layout)
        self.play_page = QWidget()
        play_layout = QVBoxLayout(self.play_page)
        play_layout.setContentsMargins(0, 0, 0, 0)
        play_row = QHBoxLayout()
        play_row.addStretch()
        play_row.addWidget(self.play_content, 1)
        play_row.addStretch()
        play_layout.addLayout(play_row, 1)
        self.menu_widget = QWidget()
        menu_layout = QVBoxLayout(self.menu_widget)
        menu_layout.setContentsMargins(0, 10, 0, 0)
        tabs = QHBoxLayout()
        tabs.addStretch()
        self.play_tab_button = QPushButton("Play", self.menu_widget)
        self.items_tab_button = QPushButton("Items", self.menu_widget)
        tab_style = f'''QPushButton {{ font-family: "{font_family}"; font-size: 14px;
            padding: 12px 22px; color: #aaa; background: black; border: none;
            border-bottom: 2px solid transparent; }}
            QPushButton:checked {{ color: white; border-bottom-color: white; }}
            QPushButton:hover, QPushButton:focus {{ color: white; background: black; }}'''
        for button in (self.play_tab_button, self.items_tab_button):
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setStyleSheet(tab_style)
            tabs.addWidget(button)
        tabs.addStretch()
        self.play_tab_button.setChecked(True)
        self.play_tab_button.clicked.connect(lambda _checked=False: self.show_menu_tab("play"))
        self.items_tab_button.clicked.connect(lambda _checked=False: self.show_menu_tab("items"))
        menu_layout.addLayout(tabs)
        self.menu_stack = QStackedLayout()
        self.menu_stack.addWidget(self.play_page)
        self.item_catalogue = ItemCataloguePanel(
            ITEM_CATALOGUE, self.profile, UNLOCK_GOALS, self.set_menu_item_enabled,
            body_font, self.menu_widget, icon_root=resource_path("icons/items"),
            title_font=self.speaker_font)
        self.item_catalogue.setMaximumWidth(1440)
        self.item_catalogue_page = QWidget()
        catalogue_row = QHBoxLayout(self.item_catalogue_page)
        catalogue_row.setContentsMargins(0, 0, 0, 0)
        catalogue_row.addStretch(1)
        catalogue_row.addWidget(self.item_catalogue, 100)
        catalogue_row.addStretch(1)
        self.menu_stack.addWidget(self.item_catalogue_page)
        menu_layout.addLayout(self.menu_stack, 1)

        self.layout = QVBoxLayout()
        self.layout.addWidget(self.weapon_scroll, 1)
        #self.layout.addWidget(self.top_right_widget)
        self.layout.addWidget(self.menu_widget, 1)
        self.layout.addWidget(self.room_buttons_widget, 1)
        self.layout.addWidget(self.conversation_stage)
        self.layout.addWidget(self.message_log, 1)
        self.conversation_stage.setMaximumWidth(1280)
        self.message_log.setMaximumWidth(1280)
        self.layout.setAlignment(self.conversation_stage, Qt.AlignHCenter)
        self.layout.setAlignment(self.message_log, Qt.AlignHCenter)
        self.layout.addWidget(self.secondary_output_area)
        self.inventory_button = QPushButton("Inventory")
        self.inventory_button.setFont(self.speaker_font)
        self.inventory_button.setStyleSheet(
            'QPushButton { font-size: 12px; color: #ddd; background: transparent; '
            'border: 1px solid #555; padding: 12px 16px; } '
            'QPushButton:enabled:hover, QPushButton:focus { color: white; border-color: #ddd; } '
            'QPushButton:disabled { color: #666; border-color: #333; }')
        self.inventory_button.clicked.connect(self.open_inventory)
        self.inventory_button.hide()
        self.progress_notice = QLabel()
        self.progress_notice.setFont(body_font)
        self.progress_notice.setStyleSheet(f'font-family: "{body_family}"; font-size: 18px;')
        self.progress_notice.setWordWrap(True)
        self.progress_notice.hide()
        self.layout.addWidget(self.progress_notice)
        self.progress_retry_button = QPushButton("Retry saving progress")
        self.progress_retry_button.setFont(body_font)
        self.progress_retry_button.setStyleSheet(f'font-family: "{body_family}"; font-size: 18px;')
        self.progress_retry_button.clicked.connect(self.retry_progress)
        self.progress_retry_button.hide()
        self.layout.addWidget(self.progress_retry_button)
        self.retry_button = QPushButton("Retry")
        self.retry_button.clicked.connect(self.retry_ai_request)
        self.retry_button.hide()
        self.layout.addWidget(self.retry_button)
        #layout.setContentsMargins(50, 50, 50, 50)

        # Placeholder for the conversation phase buttons (hide initially)
        self.conversation_buttons_widget = QWidget()
        self.conversation_buttons_widget.setLayout(self.conversation_buttons_layout)
        self.layout.addWidget(self.conversation_buttons_widget)

        # Layout for interjecting text box and button (below the output area)
        interject_layout = QVBoxLayout()
        input_row = QHBoxLayout()
        input_row.addWidget(self.recipient_controls)
        input_row.addWidget(self.interject_input, 1)
        input_row.addWidget(self.dictation_controls)
        input_row.addWidget(self.say_button)
        interject_layout.addLayout(input_row)
        interject_layout.addWidget(self.muffled_player_controls)
        interject_layout.addWidget(self.recipient_hint)
        interject_layout.addWidget(self.dictation_status)
        self.layout.addLayout(interject_layout)
        self.layout.addWidget(self.inventory_button, 0, Qt.AlignRight)

        container = QWidget()
        container.setLayout(self.layout)
        self.setCentralWidget(container)

        # Initially hide room and conversation elements
        self.room_buttons_widget.hide()
        self.conversation_buttons_widget.hide()

        # Create "How to Play" button
        self.how_to_play_button = QPushButton("How to Play", self)
        self.how_to_play_button.setStyleSheet(f"""
            QPushButton{{
            font-size: 14px; padding: 10px; color: white;
            background-color: transparent; border: none;
            }}
            QPushButton:hover {{
                color: #808080;
            }}
            """)
        self.how_to_play_button.clicked.connect(self.show_how_to_play)

        # Create "Transcript" button
        self.transcript_button = QPushButton("Transcript", self)
        corner_font = QFont(self.pixel_font_family)
        corner_font.setPixelSize(14)
        self.how_to_play_button.setFont(corner_font)
        self.transcript_button.setFont(corner_font)
        self.transcript_button.setStyleSheet(f"""
            QPushButton{{
            font-size: 14px; padding: 10px; color: white;
            background-color: rgba(0, 0, 0, 0);
            }}
            QPushButton:hover {{
                color: #808080;
            }}
            """)
        self.transcript_button.clicked.connect(self.show_transcript)
        self.transcript_button.setStyleSheet(
            'QPushButton { font-size: 12px; color: #ddd; background: black; '
            'border: 1px solid #666; padding: 10px; } '
            'QPushButton:enabled:hover, QPushButton:focus { color: white; border-color: white; }')
        self.transcript_button.hide()  # Initially hidden

        # Position the "How to Play" button in the top-right corner
        # Scale button size and positioning based on screen resolution
        button_width = int(screen_width * 0.15)  # 10% of the screen width for the button width
        button_height = int(button_width * 0.35)  # Adjust height proportionally to the width
        button_x = screen_width - (button_width + 10)  # Position button 10 pixels from the right edge
        button_y = 10  # 20 pixels from the top


        self.how_to_play_button.setGeometry(button_x, button_y, button_width, button_height)
        self.transcript_button.setGeometry(button_x, button_y, button_width, button_height)  # Adjust position and size
        self.how_to_play_button.show()
        self.how_to_play_button.raise_()
        self.transcript_button.hide()

        self.mute_button = QPushButton(self)
        self.mute_button.setStyleSheet('QPushButton { background: transparent; border: none; }')
        self.update_mute_button_icon()
        self.mute_button.clicked.connect(self.toggle_mute)
        self.mute_button.setGeometry(0, button_y, button_width // 2, button_height)
        self.mute_button.show()
        self.game_menu_button = QPushButton("Menu [Esc]", self)
        self.game_menu_button.setFont(self.speaker_font)
        self.game_menu_button.setAccessibleName("Open game menu (Escape)")
        self.game_menu_button.setStyleSheet(
            'QPushButton { font-size: 10px; color: #aaa; background: black; '
            'border: 1px solid #444; padding: 10px; } '
            'QPushButton:enabled:hover, QPushButton:focus { color: white; border-color: #ddd; }')
        self.game_menu_button.clicked.connect(self.open_escape_menu)
        self.game_menu_button.hide()

        self.showFullScreen()  # Make the window fullscreen

        # Weapon action panel (used in Action Phase)
        self.weapon_action_panel = QFrame(self.centralWidget())
        self.weapon_action_panel.setObjectName("weaponActionPanel")
        self.weapon_action_panel.setVisible(False)         # only shown during Action Phase
        self.weapon_action_panel.setFixedWidth(260)        # card width; tweak as needed
        self.weapon_action_panel.setStyleSheet("""
            #weaponActionPanel {
                border: none;
                background: transparent;
            }
        """)
        self.weapon_action_panel.setLayout(QVBoxLayout())
        self.weapon_action_panel.layout().setContentsMargins(0, 0, 0, 0)
        self.weapon_action_panel.layout().setSpacing(0)
        self.house_sidebar_layout.insertWidget(2, self.weapon_action_panel)
        self.room_buttons_widget.installEventFilter(self)
        QTimer.singleShot(0, self.position_weapon_panel)

        #Start-screen Music
        self.media_player = QMediaPlayer()
        music_url = QUrl.fromLocalFile(resource_path("sounds/start.mp3"))
        self.media_player.setMedia(QMediaContent(music_url))
        self.media_player.setVolume(50)  # Adjust volume
        self.media_player.play()

        # Make the music loop indefinitely
        self.media_player.mediaStatusChanged.connect(self.loop_music)

        # Configure the output area
        self.set_action_output_mode(False)
        self.setStyleSheet(f"color: white; background-color: black; font-family: {font_family};")
        self.resize_menu_content()
        self.position_corner_controls()

    def set_action_output_mode(self, compact):
        if hasattr(self, "conversation_stage"):
            self.conversation_stage.setVisible(not compact and self.phase == "conversation" and not self.game_over)
        self.message_log.setMaximumHeight(128 if compact else 16777215)
        background = "black"
        self.output_area.setStyleSheet(
            f'background-color: {background}; color: #E0E0E0; border: none; '
            f'font-family: "{readable_body_font().family()}"; font-size: 18px; '
            'padding-left: 16px; padding-right: 24px;')

    def refresh_conversation_stage(self):
        if not hasattr(self, "conversation_stage"):
            return
        visible = self.phase == "conversation" and not self.game_over
        self.conversation_stage.setVisible(visible)
        if visible:
            participants = [StageParticipant(self.body_id(name), self.display_actor(name),
                                             character_art_path(self.perceived_actor(name)))
                            for name, data in self.characters.items() if data["alive"]]
            additions = [participant.body_id for participant in participants
                         if participant.body_id not in self.stage_body_order]
            random.Random().shuffle(additions)
            self.stage_body_order.extend(additions)
            participants.sort(key=lambda participant: self.stage_body_order.index(participant.body_id))
            self.conversation_stage.set_participants(participants)

    def refresh_known_equipment(self):
        self.npc_equipment_label.clear()
        self.npc_equipment_label.hide()

    def resize_menu_content(self):
        if not hasattr(self, "play_content"):
            return
        available_width = min(1000, max(1, self.width() - 32))
        title_size = max(24, min(144, int(available_width * 0.09), int(self.height() * 0.12)))
        self.title_label.setStyleSheet(
            f'font-family: "{self.pixel_font_family}"; font-size: {title_size}px; color: white; padding-bottom: 2px;')
        control_size = max(16, min(32, int(available_width * 0.026), int(self.height() * 0.045)))
        control_height = max(50, min(84, int(self.height() * 0.08)))
        mode_color = "#d0c8bb" if self.show_room_allocations else "#c58e87"
        for button, color in ((self.difficulty_button, mode_color), (self.start_button, "white")):
            button.setFixedHeight(control_height)
            button.setStyleSheet(
                f'QPushButton {{ font-size: {control_size}px; padding: 10px; color: {color}; border: none; }} '
                'QPushButton:enabled:hover, QPushButton:focus { color: #aaa; background: transparent; }')

    def position_corner_controls(self):
        if not hasattr(self, "how_to_play_button") or not hasattr(self, "mute_button"):
            return
        width = min(max(self.how_to_play_button.sizeHint().width(),
                        self.transcript_button.sizeHint().width()), max(1, self.width() - 20))
        height = max(48, self.how_to_play_button.sizeHint().height(),
                     self.transcript_button.sizeHint().height())
        for button in (self.how_to_play_button, self.transcript_button):
            button.setGeometry(self.width() - width - 10, 10, width, height)
        self.mute_button.setGeometry(0, 10, width // 2, height)
        self.game_menu_button.setGeometry(12, 10, self.game_menu_button.sizeHint().width(), height)
        self.game_menu_button.setVisible(self.phase not in ("start", "closed", "restarting"))
        self.game_menu_button.raise_()
        mute_option = QStyleOptionButton()
        self.mute_button.initStyleOption(mute_option)
        mute_contents = self.mute_button.style().subElementRect(
            QStyle.SE_PushButtonContents, mute_option, self.mute_button)
        icon_size = max(1, min(64, mute_contents.width(), mute_contents.height()))
        self.mute_button.setIconSize(QSize(icon_size, icon_size))
        if self.phase == "start":
            controls = (self.how_to_play_button, self.transcript_button, self.mute_button)
            bottom = max((button.geometry().bottom() + 1 for button in controls
                          if not button.isHidden()), default=0)
            top = max(10, self.menu_widget.mapFrom(self, QPoint(0, bottom + 12)).y())
            self.menu_widget.layout().setContentsMargins(0, top, 0, 0)
        if hasattr(self, "layout"):
            top = height + 20 if self.phase in ("action", "conversation", "voting") else 11
            self.layout.setContentsMargins(11, top, 11, 11)
        if self.draft_page is not None:
            controls = (self.how_to_play_button, self.transcript_button, self.mute_button)
            bottom = max((button.geometry().bottom() + 1 for button in controls
                          if not button.isHidden()), default=0)
            top = max(0, self.draft_page.mapFrom(self, QPoint(0, bottom + 8)).y()) if bottom else 0
            self.draft_page.layout().setContentsMargins(0, top, 0, 0)

    def eventFilter(self, obj, event):
        if (event.type() in (QEvent.Hide, QEvent.Close)
                and obj in getattr(self, "_weapon_windows", {}).values()):
            self.overlay_host.resume_unlocks()
        if (obj is getattr(self, "overlay_host", None) and event.type() == QEvent.KeyPress
                and event.key() == Qt.Key_Escape and self.pause_settings_active()):
            self.show_pause_menu_page()
            return True
        if obj is self.room_buttons_widget and event.type() in (QEvent.Show, QEvent.Resize, QEvent.Move):
            QTimer.singleShot(0, self.position_weapon_panel)  # schedule after layout settles
        if obj is getattr(self, "interject_input", None) and event.type() in (QEvent.Show, QEvent.Hide, QEvent.EnabledChange):
            if event.type() == QEvent.Show:
                self.invalidate_prepared_turn("composing")
            self.update_recipient_controls()
            if self.can_dictate():
                self.update_dictation_controls()
            else:
                self.cancel_dictation()
        return super().eventFilter(obj, event)

    def player_is_muffled(self):
        return bool(self.player_name and self.item_rules.is_muffled(self.body_id(self.player_name)))

    def update_muffled_player_controls(self):
        if not hasattr(self, "muffled_player_controls"):
            return
        muffled = self.player_is_muffled()
        was_muffled = getattr(self, "_player_muffle_active", False)
        self._player_muffle_active = muffled
        conversation = self.phase == "conversation" and not self.game_over
        required = conversation and self.player_reply_required()
        self.muffled_player_controls.setVisible(muffled and required)
        self.muffled_player_tone.setEnabled(not self.ai_busy)
        if muffled:
            self.interject_input.clear()
            self.interject_input.hide()
            self.interject_input.setEnabled(False)
            self.recipient_controls.hide()
            self.recipient_selector.setEnabled(False)
            self.recipient_hint.hide()
            self.interject_button.setEnabled(False)
            self.say_button.setVisible(required)
            self.say_button.setEnabled(required and not self.ai_busy)
            if required:
                self.pass_button.setEnabled(False)
            if hasattr(self, "dictation_recorder") and self.dictation_context is not None:
                self.cancel_dictation()
        if was_muffled and not muffled:
            self.interject_input.setEnabled(not self.ai_busy)
            self.interject_button.setEnabled(conversation and not self.ai_busy)
            if required:
                self.interject_input.show()
                self.say_button.show()
        self.update_dictation_controls()

    def can_dictate(self):
        return (self.phase == "conversation" and not self.game_over and not self.ai_busy
                and not self.player_is_muffled()
                and not self.interject_input.isHidden() and self.interject_input.isEnabled())

    def can_toggle_dictation(self):
        return self.dictation_recorder.is_recording or (self.can_dictate() and self.dictation_worker is None)

    def dictation_binding_conflict(self, binding):
        if binding.kind == "key" and binding.key in (int(Qt.Key_Escape), int(Qt.Key_Return), int(Qt.Key_Enter)):
            return "This key is reserved for the game menu or sending your message."
        return None

    def show_dictation_binding_failure(self, message):
        self.dictation_status.setText(message)
        self.dictation_status.show()

    def update_dictation_controls(self):
        if not hasattr(self, "dictation_controls"):
            return
        self.dictation_controls.setVisible(self.can_dictate() or self.dictation_recorder.is_recording)
        recording = self.dictation_recorder.is_recording
        action = "Stop recording" if recording else "Record voice"
        icon = self.dictation_icons[recording]
        self.dictation_button.setIcon(icon)
        self.dictation_button.setText("" if not icon.isNull() else ("STOP" if recording else "REC"))
        self.dictation_button.setChecked(recording)
        self.dictation_button.setAccessibleName(action)
        self.dictation_button.setToolTip("")
        self.dictation_glow.setEnabled(recording)
        self.dictation_button.setEnabled(self.can_toggle_dictation())
        self.dictation_cancel_button.setVisible(self.dictation_context is not None)

    def remember_entered_player_name(self, text):
        self.entered_player_name = text

    def save_player_name_preference(self):
        saved = self.preferences.save_player_name(self.entered_player_name)
        if self.phase == "start":
            self.player_name_input.setToolTip("" if saved else "Your name could not be remembered for next time.")
        return saved

    def record_dictation_diagnostic(self, metadata):
        if self.trace.enabled:
            self.trace.record("audio_capture", {"source": "local_recorder", "round": self.round_number,
                                                "phase": self.phase, **metadata})

    def toggle_dictation(self):
        if self.dictation_recorder.is_recording:
            if self.can_dictate():
                self.dictation_recorder.stop()
            else:
                self.cancel_dictation()
            return
        if not self.can_dictate() or self.dictation_worker is not None:
            return
        self.dictation_session += 1
        self.dictation_context = (self.dictation_session, self.round_number)
        self.dictation_status.clear()
        self.dictation_status.hide()
        try:
            self.dictation_recorder.start()
        except DictationError:
            self.handle_dictation_failure(self.dictation_recorder.generation,
                                          "Could not start recording. Please try again.")

    def dictation_is_current(self, generation, context):
        return (context is not None and self.dictation_context == context
                and context == (self.dictation_session, self.round_number)
                and self.dictation_recorder.is_current(generation) and self.can_dictate())

    def handle_dictation_started(self, generation):
        if self.dictation_is_current(generation, self.dictation_context):
            self.dictation_status.setText("Recording... Stop when ready (up to 60 seconds).")
            self.dictation_status.show()
            self.update_dictation_controls()

    def transcribe_recording(self, generation, wav_audio):
        context = self.dictation_context
        if not self.dictation_is_current(generation, context) or self.dictation_worker is not None:
            return
        runtime = self.ai_runtime
        worker = AIWorker(lambda: runtime.transcribe(wav_audio))
        self.dictation_worker = worker
        self.dictation_status.setText("Transcribing...")
        self.dictation_status.show()
        self.update_dictation_controls()

        def ready(text):
            if not self.dictation_is_current(generation, context):
                return
            if not isinstance(text, str) or not text.strip():
                self.handle_dictation_failure(generation, "No speech was transcribed. Please try again.", context)
                return
            draft = self.interject_input.text()
            addition = (" " if draft and not draft[-1].isspace() else "") + text.strip()
            if len((draft + addition).encode("utf-16-le", "surrogatepass")) // 2 > self.interject_input.maxLength():
                self.handle_dictation_failure(generation, "This recording will not fit in the draft. Shorten your draft and try again.", context)
                return
            self.interject_input.end(False)
            self.interject_input.insert(addition)
            self.dictation_context = None
            self.dictation_status.setText("Added to your draft. Edit it, then choose Say.")
            self.dictation_status.show()
            self.update_dictation_controls()

        def finished():
            if self.dictation_worker is worker:
                self.dictation_worker = None
                self.update_dictation_controls()

        # Unsubmitted speech and microphone bytes never enter gameplay traces.
        worker.result_ready.connect(ready)
        worker.failed.connect(lambda message: self.handle_dictation_failure(generation, message, context))
        worker.finished.connect(finished)
        self.start_worker(worker)

    def handle_dictation_failure(self, generation, message, context=None):
        context = self.dictation_context if context is None else context
        if self.dictation_is_current(generation, context):
            self.dictation_context = None
            self.dictation_status.setText(message)
            self.dictation_status.show()
            self.update_dictation_controls()

    def cancel_dictation(self):
        self.dictation_session += 1
        self.dictation_context = None
        self.dictation_recorder.cancel()
        if hasattr(self, "dictation_status"):
            self.dictation_status.clear()
            self.dictation_status.hide()
        self.update_dictation_controls()

    def update_room_button_states(self):
        # Accessibility and painting reflect the same existing gameplay gates.
        records = tuple(RoomObservation(
            room_id, available=self.room_buttons[label].isEnabled(),
            known_names=self.house_view.observations[room_id].known_names,
            unknown_remainder=self.house_view.observations[room_id].unknown_remainder,
            observed_at=self.house_view.observations[room_id].observed_at,
            light_on=self.house_view.observations[room_id].light_on,
        ) for room_id, label in ROOM_LABELS.items())
        self.house_view.set_observations(records)

    def update_mute_button_icon(self):
        """Update the mute button icon based on whether sound is enabled."""
        if self.sfx_enabled:
            self.mute_button.setIcon(QIcon(resource_path("icons/audio.png")))  # Sound on icon
        else:
            self.mute_button.setIcon(QIcon(resource_path("icons/audio-off.png")))  # Muted icon

    def toggle_mute(self):
        """Toggle sound on/off and update icon."""
        self.sfx_enabled = not self.sfx_enabled
        self.music_enabled = not self.music_enabled

        if self.music_enabled:
            self.media_player.play()  
        else:
            self.media_player.pause() 
            self.effect_player.pause()

        # Update the button icon
        self.update_mute_button_icon()

    def loop_music(self, status):
        # Check if music has ended, and if so, restart it
        if status == QMediaPlayer.EndOfMedia and self.music_enabled:
            self.media_player.play()

    def play_sound_effect(self, sound_path):
        """Plays the given sound effect, if sound is enabled."""
        if self.sfx_enabled:
            self.effect_player.setMedia(QMediaContent(QUrl.fromLocalFile(resource_path(sound_path))))
            self.effect_player.play()

    def toggle_mode(self):
        # Toggle between Normal and Super Hard modes
        if self.show_room_allocations:
            self.difficulty_button.setText("Mode: Super Hard  >")
            self.show_room_allocations = False
        else:
            self.difficulty_button.setText("Mode: Coward  >")
            self.show_room_allocations = True
        self.resize_menu_content()

    def start_game(self):
        if self.phase != "start":
            return
        self.save_player_name_preference()
        self.player_name = self.player_name_input.text().strip().capitalize().split(" ")[0]
        if not self.player_name:
            self.player_name_label.setText("Please enter your name to start the game.")
            self.player_name_input.setFocus()
            return
        self.how_to_play_button.hide()
        self.menu_widget.hide()

        # Hide name input, start button, and difficulty button
        self.start_button.hide()
        self.player_name_label.hide()
        self.player_name_input.hide()
        self.difficulty_button.hide()
        self.title_label.hide()

        # Show room buttons and output area
        self.room_buttons_widget.show()
        self.message_log.show()

        # Initialize characters
        self.characters = {
            "Jerry": {"alive": True, "profile": ["you MUST never reveal that your 'wife' is actually a dead rat that you found and your 'children' are the droppings you found next to it (non-negotiable)"], "experiences": [], "dispositions": []},
            "Dave": {"alive": True, "profile": ["you MUST constantly be offended by the statements of others (this is non-negotiable)"], "experiences": [], "dispositions": []},
            "Owen": {"alive": True, "profile": ["you MUST be overly-flirtatious and full of flattery, especially directed towards Jerry (this is non-negotiable)"], "experiences": [], "dispositions": []},
            "Hank": {"alive": True, "profile": ["You are easygoing, sociable, and wholehearted about keto. You believe it changed your life, and you are eager for everyone else to discover what you think they are missing. That devotion shapes your priorities, the connections you make, and how you try to bring people around. Respond to the situation as a person whose enthusiasm is part of how they think and relate to others. Let the conversation and your own concerns determine when that enthusiasm comes to the surface."], "experiences": [], "dispositions": []},
            "Debra": {"alive": True, "profile": ["you may only speak in riddles (this is non-negotiable)"], "experiences": [], "dispositions": []}
        }

        for character in self.characters:
            self.characters[character]["muffle_style"] = character
            if character in self.player_name or self.player_name in ("David", "Debbie", "Detective"):
                self.player_name = "Einstein"

        self.old_crime_scenes = []
        self.round_number = 0
        self.game_over = False
        self.ensure_body_ids()
        self.ensure_identity_state()
        self.load_profile()
        self.npc_items = NPCItemState.allocate(
            name for name, data in self.characters.items() if data["alive"]
        )

        # Start the first action phase
        self.start_development_trace()
        self.show_weapon_select_screen()


    def show_weapon_select_screen(self):
        """Populate and show a grid of square weapon buttons with image + name; tooltip on hover."""
        self.invalidate_prepared_turn("weapon_selection", changed=True)
        self.cancel_item_offer()
        self.phase = "weapon_select"
        self.cancel_dictation()
        self.menu_widget.hide()
        self.npc_equipment_label.hide()
        self.mute_button.hide()
        self.message_log.hide()
        self.room_buttons_widget.hide()
        # Clear previous (if restarting)
        while self.weapon_grid.count():
            item = self.weapon_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        available_width = max(240, self.centralWidget().width() - 60)
        columns = max(1, min(4, available_width // 300))
        card_w = min(320, (available_width - 16 * (columns - 1)) // columns)
        card_h = 420
        icon_sz = min(200, card_w - 32)

        for i, wpn in enumerate(self.weapons):
            row = i // columns
            col = i % columns
            unlocked = wpn["id"] in available_content(self.profile, "weapon")

            card = QFrame(self.weapon_picker_widget)
            card.setFixedSize(QSize(card_w, card_h))
            card.setCursor(Qt.PointingHandCursor)
            card.setFocusPolicy(Qt.NoFocus)
            card.setObjectName("weaponCard")
            card.setStyleSheet("""
                QFrame#weaponCard {
                    border: 1px solid #555;
                    background-color: #090909;
                }
            """ + ("""
                QFrame#weaponCard:hover {
                    background-color: #151515;
                    border-color: #ddd;
                }
            """ if unlocked else ""))

            v = QVBoxLayout(card)
            v.setContentsMargins(10, 10, 10, 10)
            v.setSpacing(8)

            # --- Image ---
            img = QLabel(card)
            img.setAlignment(Qt.AlignCenter)
            img.setFocusPolicy(Qt.NoFocus)
            img.setStyleSheet("border: none; background: transparent;")  # no outline
            pix = QPixmap(resource_path(wpn.get("image", "")))
            if pix.isNull():
                ph = QPixmap(icon_sz, icon_sz); ph.fill(Qt.transparent); pix = ph
            img.setPixmap(pix.scaled(icon_sz, icon_sz, Qt.KeepAspectRatio, Qt.FastTransformation))
            # Make child transparent to mouse so the FRAME gets hover/click even over the image
            img.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            v.addWidget(img, alignment=Qt.AlignHCenter)

            # --- Name ---
            name = QLabel(wpn["name"], card)
            name.setAlignment(Qt.AlignHCenter)
            name.setFocusPolicy(Qt.NoFocus)
            name.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {'white' if unlocked else '#888'}; border: none; background: transparent;")
            name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            v.addWidget(name)

            # --- Description (smaller, grey, wrapped) ---
            description = wpn["desc"]
            if not unlocked:
                description += "\n\nLocked: " + UNLOCK_GOALS[wpn["id"]]
            desc = QLabel(description, card)
            desc.setWordWrap(True)
            desc.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            desc.setFocusPolicy(Qt.NoFocus)
            desc.setFont(readable_body_font())
            desc.setStyleSheet(f'font-family: "{readable_body_font().family()}"; font-size: 18px; color: #B8B8B8; border: none; background: transparent;')
            desc.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            v.addWidget(desc, stretch=1)

            # Click anywhere on the card to select
            def _click_handler(_evt=None, wpn=wpn, allowed=unlocked):
                if allowed:
                    self.select_weapon_and_start(wpn)
            if not unlocked:
                card.setCursor(Qt.ArrowCursor)
            card.mousePressEvent = _click_handler

            self.weapon_grid.addWidget(card, row, col)

        self.weapon_scroll.show()


    def select_weapon_and_start(self, weapon):
        if self.phase != "weapon_select":
            return
        self.load_profile()
        allowed = available_content(self.profile, "weapon")
        if weapon not in self.weapons or weapon["id"] not in allowed:
            return
        self.run_weapons = allowed
        self.run_item_pool = tuple(item_id for item_id in available_content(self.profile, "item")
                                   if item_id not in OPTIONAL_ITEM_NARRATION_PENDING)
        self.item_drafts = DraftState(self.player_owner_id, self.run_item_pool)
        self.inventory_button.show()
        self.selected_weapon = weapon
        self.trace_action("weapon_selected", weapon_id=weapon["id"])
        self.record_history(f"Selected weapon: {weapon['name']}")
        self.weapon_scroll.hide()
        self.npc_equipment_label.hide()

        # weapon-specific one-time flags
        wid = weapon["id"]
        self.stopwatch_selected = (wid == "stopwatch")
        if wid != "voodoo":
            self.voodoo_current_identity = None

        # proceed into the normal loop
        self.message_log.show()
        self.room_buttons_widget.show() 
        self.run_action_phase()
        self.overlay_host.resume_unlocks()

    def show_how_to_play(self):
        if self.overlay_host.current_page is not None or self.separate_surface_open():
            return
        page = HowToPlayPage()
        if self.open_overlay_page(page, "How to Play"):
            self.help_page = page

    def show_transcript(self):
        if self.overlay_host.current_page is not None or self.separate_surface_open():
            return
        page = MessageLog(QTextEdit(), body_font=readable_body_font(), speaker_font=self.speaker_font)
        page.editor.setStyleSheet(self.output_area.styleSheet())
        page.set_records(self.history_records)
        if self.open_overlay_page(page, "Transcript"):
            self.transcript_page = page
            self.history_record_added.connect(page.append_record)

    def open_overlay_page(self, page, title, help_text=""):
        fit_window_to_screen(self.overlay_host, 800, 700)
        if not self.separate_surface_open() and self.overlay_host.open_page(page, title=title, help_text=help_text):
            return True
        page.deleteLater()
        return False

    def separate_surface_open(self):
        return any(surface is not None and surface.isVisible()
                   for surface in self._weapon_windows.values())

    def build_weapon_action_panel(self):
        """Right-side card for the currently equipped weapon during Action Phase."""
        if self.phase != "action" or self.game_over or self.pending_room_action is not None or self.bonus_action_pending:
            self.weapon_action_panel.hide()
            return
        # clear panel
        lay = self.weapon_action_panel.layout()
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

        # guard
        wpn = getattr(self, "selected_weapon", None)
        if not wpn:
            self.weapon_action_panel.hide()
            return

        self.weapon_action_panel.show()

        card_w, card_h, icon_sz = 240, 260, 120

        # --- Card ---
        card = QFrame(self.weapon_action_panel)
        card.setFixedSize(QSize(card_w, card_h))
        card.setCursor(Qt.PointingHandCursor)
        interactive = wpn["id"] in ("pen", "blackmagic", "knife", "c4") and not (
            wpn["id"] == "c4" and self.c4_cooldown_nights > 0
        )

        # Passive weapons: no hover effect or pointer
        if not interactive:
            card.setCursor(Qt.ArrowCursor)

        card.setStyleSheet(f"""
            QFrame {{
                border: 1px solid #555;
                border-radius: 16px;
                background-color: rgba(255,255,255,0.04);
            }}
            {'QFrame:hover { background-color: rgba(255,255,255,0.10); border-color: #777; }' if interactive else ''}
        """)

        if wpn["id"] == "knife" and self.knife_extra_target:
            card.setStyleSheet(f"""
                QFrame {{
                    border: 2px solid #C62828; 
                    border-radius: 16px;
                    background-color: rgba(198,40,40,0.12);
                }}
                {'QFrame:hover { background-color: rgba(198,40,40,0.18); border-color: #EF5350; }' if interactive else ''}
            """)

        v = QVBoxLayout(card)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        # Image
        img = QLabel(card)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet("border:none;background:transparent;")
        pix = QPixmap(resource_path(wpn.get("image", "")))
        if pix.isNull():
            ph = QPixmap(icon_sz, icon_sz); ph.fill(Qt.transparent); pix = ph
        img.setPixmap(pix.scaled(icon_sz, icon_sz, Qt.KeepAspectRatio, Qt.FastTransformation))
        img.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        v.addWidget(img, alignment=Qt.AlignHCenter)

        # Name
        name = QLabel(wpn["name"], card)
        name.setObjectName("actionWeaponName")
        name.setAlignment(Qt.AlignHCenter)
        name.setStyleSheet("font-size:18px;font-weight:600;color:white;border:none;background:transparent;")
        name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        v.addWidget(name)

        # Description (depends on weapon)
        if wpn["id"] == "pen":
            desc_text = "Write note."
        elif wpn["id"] == "blackmagic":
            desc_text = "Choose victims."
        elif wpn["id"] == "knife":
            if self.knife_extra_target:
                desc_text = f"\nACTIVE \n({self.perceived_actor(self.knife_extra_target)})"
            else:
                desc_text = "Choose extra victim. \n (optional)"
        elif wpn["id"] == "c4":
            desc_text = "Plant explosives." if self.c4_cooldown_nights == 0 else f"On Cooldown. \n (that stuff aint cheap)"
        else:
            desc_text = "No active effect."

        desc = QLabel(desc_text, card)
        desc.setObjectName("actionWeaponDescription")
        description_font = readable_body_font()
        desc.setFont(description_font)
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        description_color = "#EF5350" if self.knife_extra_target else "#A0A0A0"
        desc.setStyleSheet(f'font-family: "{description_font.family()}"; font-size: 18px; '
                          f'color:{description_color};border:none;background:transparent;')
        desc.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        v.addWidget(desc, stretch=1)

        # Click behavior
        def on_card_click(_evt=None):
            if not interactive:
                return
            if wpn["id"] == "pen":
                self.open_pen_editor()
            elif wpn["id"] == "blackmagic":
                self.open_blackmagic_dialog()
            elif wpn["id"] == "knife":
                self.open_knife_dialog()
            elif wpn["id"] == "c4":
                self.open_c4_dialog()

        card.mousePressEvent = on_card_click

        # mount
        lay.addWidget(card, alignment=Qt.AlignTop)

        # mandatory gating
        self.apply_weapon_gating()

        QTimer.singleShot(0, self.position_weapon_panel)
        self.weapon_action_panel.raise_()

    def position_weapon_panel(self):
        """The sidebar layout owns geometry; existing phase gates own visibility."""
        if not getattr(self, "weapon_action_panel", None):
            return
        # Fit the composition to the proportional artwork instead of the monitor width.
        height = max(1, self.room_buttons_widget.contentsRect().height() - 16)
        art_width = int(height * ARTWORK_SIZE[0] / ARTWORK_SIZE[1])
        row_width = art_width + 16 + self.house_sidebar.width() + self.house_row.layout().spacing()
        self.house_row.setFixedWidth(min(self.room_buttons_widget.contentsRect().width(), row_width))
        self.room_buttons_widget.layout().activate()
        self.house_row.layout().activate()
        visible = (self.phase == "action" and not self.game_over and bool(self.selected_weapon)
                   and self.pending_room_action is None and not self.bonus_action_pending
                   and not self.room_buttons_widget.isHidden())
        self.weapon_action_panel.setVisible(visible)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_menu_content()
        self.position_corner_controls()
        self.position_weapon_panel()

    # Weapon-gating logic
    def can_configure_weapon(self, weapon_id, editor=None, night=None):
        """Only current setup may change before a room action is accepted."""
        return (self.phase == "action" and not self.game_over
                and self.pending_room_action is None and not self.bonus_action_pending
                and (self.selected_weapon or {}).get("id") == weapon_id
                and (editor is None or (self._weapon_windows.get(weapon_id) is editor
                                        and night == self.round_number)))

    def retire_weapon_editors(self):
        for weapon_id, editor in tuple(self._weapon_windows.items()):
            if editor is not None:
                self._weapon_windows[weapon_id] = None
                editor.close()
                editor.deleteLater()

    def forget_weapon_editor(self, weapon_id, editor):
        if self._weapon_windows.get(weapon_id) is editor:
            self._weapon_windows[weapon_id] = None
            self.overlay_host.resume_unlocks()

    def weapon_is_mandatory(self):
        if self.bonus_action_pending:
            return False
        wpn = getattr(self, "selected_weapon", None)
        if not wpn:
            return False
        if wpn["id"] == "c4":
            return self.c4_cooldown_nights == 0  # mandatory only when usable
        return wpn["id"] in ("pen", "blackmagic")

    def weapon_configured(self):
        wpn = getattr(self, "selected_weapon", None)
        if not wpn:
            return True
        if wpn["id"] == "pen":
            return bool(self.pending_pen_note_text)
        if wpn["id"] == "blackmagic":
            return bool(self.black_magic_controller and self.black_magic_target and self.black_magic_controller != self.black_magic_target)
        if wpn["id"] == "c4":
            # mandatory: must plant a room this night
            return (self.c4_cooldown_nights > 0) or bool(self.c4_target_room)
        # Knife / Stopwatch / Love are optional passives
        return True

    def set_room_buttons_enabled(self, enabled: bool):
        enabled = enabled and self.pending_room_action is None
        unavailable = set(self.old_crime_scenes)
        if not self.bonus_action_pending:
            unavailable.update(getattr(self, "new_crime_scenes", []))
        for room, btn in self.room_buttons.items():
            btn.setEnabled(enabled and room not in unavailable)
        self.update_room_button_states()

    def apply_weapon_gating(self):
        if self.weapon_is_mandatory():
            self.set_room_buttons_enabled(self.weapon_configured())
        else:
            self.set_room_buttons_enabled(True)

    def mandatory_weapon_reset(self):
        self.pending_pen_note_text = None
        self.black_magic_controller = None
        self.black_magic_target = None
        self.knife_extra_target = None
        self.knife_used_bloodlust = False
        self.c4_target_room = None
        self.victims_last_night = []
        self.pen_last_victim_name = None

        # Tick down C4 cooldown if active
        if self.c4_cooldown_nights > 0:
            self.c4_cooldown_nights -= 1

    def run_action_phase(self):
        if self.game_over:
            return
        self.cancel_item_offer()
        self.invalidate_prepared_turn("night_started", changed=True)
        self.phase = "action"
        self.set_action_output_mode(True)
        self.position_corner_controls()
        self.cancel_dictation()
        self.request_serial += 1
        self.night_rooms = tuple(r for r in self.rooms if r not in self.old_crime_scenes)
        self.mandatory_weapon_reset()
        self.mute_button.hide()
        self.media_player.stop()
        self.effect_player.setVolume(25)
        self.play_sound_effect("sounds/start_button.mp3")
        self.message_log.clear()
        self.round_number += 1
        self.advance_love_histories(discussion=False)
        self.night_attacks = []
        self._night_memories_written = False
        self.night_memory_views = ()
        self.bonus_action_pending = False
        self.bonus_action_id = None
        self._night_known_alive_bodies = tuple(
            body.body_id for body in self.body_states() if body.is_npc and body.alive)
        self.apply_item_events(self.item_rules.begin_night(self.round_number, self.body_states()))
        if self.resolve_action_curses():
            return
        self.update_inventory_button()
        self.record_history(f"Night {self.round_number}", kind="section", display=True)

        living_bodies = tuple(body for body in self.body_states() if body.is_npc and body.alive)
        available_rooms, reopened = room_capacity(self.rooms, self.old_crime_scenes, len(living_bodies))
        self.old_crime_scenes = [room for room in self.old_crime_scenes if room not in reopened]
        self.night_rooms = available_rooms
        if reopened:
            self.record_history("Reopened rooms: " + ", ".join(reopened) + ".", display=True)
        reserved = self.item_rules.reserved_room_assignments(
            self.round_number, self.body_states(), available_rooms)
        planned_rooms = assign_guest_rooms(
            tuple(body.body_id for body in living_bodies), available_rooms, reserved,
            first_night=self.round_number == 1)
        self.night_arrivals = NightArrivals(self.round_number, planned_rooms)
        self._arrival_entry_events = set()
        self._arrival_memory_receipts = set()
        self._arrival_known_corpse_rooms = {}
        self._arrival_observed_bodies = {room: [] for room in self.rooms}
        self._player_entry_receipts = set()
        self._arrival_finalized = False
        self._room_choice_in_progress = False
        self.room_assignments = {room: [] for room in self.rooms}
        self.house_view.reset_observations(tuple(
            RoomObservation(room_id, unknown_remainder=not self.show_room_allocations)
            for room_id in ROOM_LABELS))
        for event in self.night_arrivals.initial:
            if self.apply_arrival(event, observed=self.show_room_allocations):
                return
        self.refresh_known_equipment()
        self.trace_action("night_arrivals_started", arrivals=self.night_arrivals.events,
                          initial_count=self.night_arrivals.initial_count)
        if self.show_room_allocations:
            assignments_output = "\n".join(
                f"{room}: {', '.join(self.room_actor_labels(room, occupants)) or 'EMPTY'}"
                for room, occupants in self.room_assignments.items() if room in available_rooms)
            self.record_history(f"Initial room arrivals;\n{assignments_output}")
        self.update_arrival_controls()

        # Ask the player to choose a room
        self.append_output_text("Choose a room in the house. Point at a room to inspect it.")
        self.rooms_visited = []  # Track rooms visited during the night
        self.new_crime_scenes = []  # Track new crime scenes
        self.had_to_change_rooms = False  # Track if the player moved rooms

        for room in self.rooms:
            if room in self.old_crime_scenes:
                self.room_buttons[room].setEnabled(False)  # Disable crime scene rooms
            else:
                self.room_buttons[room].setEnabled(True)  # Enable non-crime scene rooms

        self.update_room_button_states()

        self.room_buttons_widget.show()  # Show room selection buttons
        self.build_weapon_action_panel()  # build right-side card
        self.apply_weapon_gating()        # enforce mandatory fill-ins
        self.layout.activate()
        self.position_weapon_panel()
        self.weapon_action_panel.raise_()


    def update_arrival_controls(self):
        state = self.night_arrivals
        if state is None:
            self.loiter_button.hide()
            self.arrival_clock.hide()
            self.arrival_status.hide()
            return
        self.arrival_clock.setText(state.clock)
        self.arrival_clock.show()
        self.arrival_status.show()
        self.loiter_button.show()
        waiting = (self.phase == "action" and not self.game_over
                   and self.pending_room_action is None and state.committed_room is None)
        exhaustion_known = state.complete and (self.show_room_allocations or state.exhausted_observed)
        self.loiter_button.setEnabled(waiting and not exhaustion_known)
        if state.committed_room is not None:
            self.arrival_status.setText("You have entered the house.")
        elif exhaustion_known:
            self.arrival_status.setText("All guests have arrived. Choose a room to enter.")
        else:
            self.arrival_status.setText("Enter a room now, or wait outside for the next guest.")
        try:
            self.loiter_button.clicked.disconnect()
        except TypeError:
            pass
        night, cursor = state.night, state.cursor
        self.loiter_button.clicked.connect(
            lambda _checked=False, day=night, index=cursor: self.loiter_outside(day, index))

    def loiter_outside(self, night, cursor):
        state = self.night_arrivals
        if (state is None or state.night != night or state.cursor != cursor
                or self.phase != "action" or self.game_over or self.pending_room_action is not None
                or state.committed_room is not None or self.room_buttons_widget.isHidden()):
            return
        event = state.advance(f"night:{night}:wait:{cursor}")
        if event is not None:
            self.apply_arrival(event, observed=True, outside=True)
        self.update_arrival_controls()

    def remember_arrival(self, event_id, body_id, text, *, source, speaker_body_id=None, observed_dead_body_id=None):
        """Persist one private perception to the mind present when it occurred."""
        self.ensure_identity_state()
        mind_id = self.identity_state.mind_for_body(body_id)
        receipt = (event_id, mind_id, source)
        if receipt in self._arrival_memory_receipts:
            return
        self._arrival_memory_receipts.add(receipt)
        if mind_id == self.player_owner_id:
            self.record_history(text, display=True)
            if observed_dead_body_id is not None:
                self.observe_night_death(observed_dead_body_id)
        else:
            self.mind_data[mind_id]["experiences"].append(
                MemoryRecord(self.round_number, "experience", text, source))
            self.invalidate_prepared_turn("arrival_memory_added", changed=True)
        self.trace.record("arrival_memory_added", {
            "event_id": event_id, "recipient_body_id": body_id, "recipient_mind_id": mind_id,
            "source": source, "speaker_body_id": speaker_body_id, "text": text})

    def arrival_is_visible(self, room, *, outside=False):
        state = self.night_arrivals
        return outside or (state.committed_room is None and self.show_room_allocations) or state.committed_room == room

    def apply_arrival(self, event, *, observed=False, outside=False):
        """Apply one physical entry once; only disclosed observations reach the player."""
        if event.event_id in self._arrival_entry_events:
            return self.game_over
        self._arrival_entry_events.add(event.event_id)
        guest = self.label_for_body(event.body_id)
        if not self.characters.get(guest, {}).get("alive"):
            return self.game_over
        self.room_assignments[event.room].append(guest)
        if outside:
            self.remember_arrival(event.event_id, event.body_id,
                f"At {event.time}, you saw {self.perceived_actor(self.player_name)} outside the house as you {event.route}. They glanced towards you.",
                source="eyewitness")
        room = event.room
        reports = []
        while True:
            visible = observed if room == event.room else self.arrival_is_visible(room, outside=outside)
            if visible:
                self.project_arrival(replace(event, room=room))
                self.record_history(
                    f"At {event.time}, {self.perceived_name(event.body_id)} entered the {room}.", display=True)
            corpses = tuple(record for record in self.item_deaths.values()
                            if record.day == self.round_number and record.room_id == room)
            if not corpses:
                break
            known = self._arrival_known_corpse_rooms.setdefault(event.body_id, set())
            known.add(room)
            living_ids = tuple(body.body_id for body in self.body_states() if body.is_npc and body.alive)
            destination, reopened = flight_destination(
                self.rooms, self.old_crime_scenes, self.night_arrivals.destinations,
                living_ids, event.body_id, known)
            for reopened_room in reopened:
                self.old_crime_scenes.remove(reopened_room)
                self.night_rooms = tuple(room for room in self.rooms if room not in self.old_crime_scenes)
                self.room_buttons[reopened_room].setEnabled(True)
                self.record_history(f"Reopened room: {reopened_room}.", display=True)
            for corpse in corpses:
                victim = corpse.observed_name or self.perceived_name(corpse.body_id)
                self.remember_arrival(f"{event.event_id}:discovery:{room}:{corpse.body_id}", event.body_id,
                    f"At {event.time}, you stepped into the {room}. Blood and scattered remains lay across the floor. Among them, you recognised {victim}; their body had been torn apart. You backed out of the room and hurried to the {destination}.",
                    source="eyewitness")
                reports.append((room, corpse.body_id, victim))
            self.room_assignments[room].remove(guest)
            if visible:
                self._arrival_observed_bodies[room].remove(event.body_id)
                self.project_arrival_departure(room, event.time)
            self.night_arrivals.destinations[event.body_id] = destination
            self.room_assignments[destination].append(guest)
            self.trace_action("arrival_fled_corpse", arrival=event, room=room, destination=destination)
            room = destination
        terminal = self.resolve_room_entry_items(
            f"{self.run_id}:{event.event_id}:entry:{room}", room, {event.body_id}, disclose=visible)
        self.trace_action("night_arrival_resolved", arrival=event, room=room, observed=visible)
        if not terminal and self.characters.get(guest, {}).get("alive"):
            recipients = [self.body_id(name) for name in self.room_assignments[room]
                          if name != guest and self.characters.get(name, {}).get("alive")]
            if self.night_arrivals.committed_room == room and self.player_alive:
                recipients.append(self.body_id(self.player_name))
            discoverer = self.perceived_name(event.body_id)
            for original_room, corpse_body_id, victim in reports:
                for recipient in recipients:
                    self._arrival_known_corpse_rooms.setdefault(recipient, set()).add(original_room)
                    self.remember_arrival(
                        f"{event.event_id}:report:{original_room}:{corpse_body_id}", recipient,
                        f"At {event.time}, {discoverer} told you what they had found in the {original_room}. They described {victim}'s body torn apart, with blood and scattered remains across the floor.",
                        source="hearsay", speaker_body_id=event.body_id, observed_dead_body_id=corpse_body_id)
            self.night_arrival_resolved.emit({"arrival": event, "room": room,
                                             "corpse_body_ids": tuple(body_id for _, body_id, _ in reports)})
        self.update_room_button_states()
        return terminal

    def prepare_room_entry(self, selected_room):
        """Commit before rewards, finish unseen arrivals, then take the camera snapshot."""
        state = self.night_arrivals
        key = (self.round_number, len(self.rooms_visited), selected_room)
        if key in self._player_entry_receipts:
            return True
        if state is not None and state.committed_room is None:
            state.commit(selected_room)
        self.update_arrival_controls()
        self._player_entry_receipts.add(key)
        if self.resolve_room_entry_items(
                f"{self.run_id}:night:{self.round_number}:player:{len(self.rooms_visited)}:{selected_room}",
                selected_room, {self.body_id(self.player_name)}):
            return False
        for record in self.item_deaths.values():
            if (record.day == self.round_number and record.room_id == selected_room
                    and record.attempt_id not in self._disclosed_item_deaths):
                label = record.observed_name or self.perceived_name(record.body_id)
                self.record_history(f"{record.cause.value}: {label} died.", display=True)
                self._disclosed_item_deaths.add(record.attempt_id)
                self.observe_night_death(record.body_id)
        if state is not None and not self._arrival_finalized:
            for event in state.finish():
                if self.apply_arrival(event, observed=event.room == selected_room):
                    return False
            bodies = self.body_states()
            living = tuple(body.label for body in bodies if body.alive)
            photographers = tuple(body.label for body in bodies if body.is_npc and body.alive)
            camera_rooms = {room: list(names) for room, names in self.room_assignments.items()}
            camera_rooms[selected_room].append(self.player_name)
            self.npc_items.capture_night(
                self.round_number, camera_rooms, living, eligible_photographers=photographers,
                appearance_names={name: self.perceived_actor(name) for name in self.body_ids},
                body_ids=self.body_ids)
            self._arrival_finalized = True
            self.trace_action("arrival_camera_boundary", room=selected_room, assignments=camera_rooms)
        self.observe_house_room(selected_room, tuple(
            name for name in self.room_assignments[selected_room]
            if self.characters.get(name, {}).get("alive")))
        self.update_arrival_controls()
        return True

    def project_arrival_departure(self, room, time):
        records = []
        for room_id, label in ROOM_LABELS.items():
            old = self.house_view.observations[room_id]
            if label == room:
                names = self.room_actor_labels(room, tuple(
                    self.label_for_body(body_id) for body_id in self._arrival_observed_bodies[room]))
                old = replace(old, known_names=names, observed_at=time)
            records.append(old)
        self.house_view.set_observations(records)

    def project_arrival(self, event):
        if event.body_id not in self._arrival_observed_bodies[event.room]:
            self._arrival_observed_bodies[event.room].append(event.body_id)
        self.project_arrival_departure(event.room, event.time)

    def choose_room(self, selected_room):
        if (self.phase != "action" or self.game_over or self.pending_room_action is not None
                or self._room_choice_in_progress or selected_room not in self.room_buttons
                or self.room_buttons_widget.isHidden()
                or not self.room_buttons[selected_room].isEnabled()):
            return
        self._room_choice_in_progress = True
        try:
            if not self.prepare_room_entry(selected_room):
                return
            if self.bonus_action_pending:
                self.finish_room_choice(selected_room)
            else:
                self.offer_room_item(selected_room, lambda: self.finish_room_choice(selected_room))
        finally:
            self._room_choice_in_progress = False

    def finish_room_choice(self, selected_room):
        if (self.phase != "action" or self.game_over or self.pending_room_action is not None
                or self.room_buttons_widget.isHidden()
                or not self.room_buttons[selected_room].isEnabled()):
            return
        if not self.prepare_room_entry(selected_room):
            return
        self.record_history(f"\nYou have chosen to stay in the {selected_room}.\n", display=True)
        occupants = [name for name in self.room_assignments[selected_room]
                     if self.characters.get(name, {}).get("alive")]

        self.rooms_visited.append(selected_room)
        self.new_crime_scenes.append(selected_room)
        self.trace_action("room_entered", room=selected_room, occupants=list(occupants))

        wpn_id = None if self.bonus_action_pending else (self.selected_weapon or {}).get("id")

        # ---- Special weapons decouple sleep room from kill ----
        if wpn_id in ("c4", "blackmagic", "love"):
            self.room_buttons_widget.hide()
            self.weapon_action_panel.hide()

            if wpn_id == "love":
                # No kill. Romantic vouches with co-occupants.
                if occupants:
                    for occ in occupants:
                        if self.characters.get(occ, {}).get("alive"):
                            # timeless key-takeaway; strong, positive
                            if len(occupants) == 1:
                                self.remember(occ, "disposition", f"You and {self.perceived_actor(self.player_name)} shared a wonderful, intimate night in the {selected_room} on a previous night. It was a beautiful dance of passion and devotion and you're eager to spend more time with them. You VOUCH for {self.perceived_actor(self.player_name)}.")
                            else: 
                                others = [self.perceived_actor(o) for o in occupants if o != occ and self.characters[o]["alive"]]
                                others.append(self.perceived_actor(self.player_name))
                                self.remember(occ, "disposition", f"You and " + " and ".join(others) + f" shared a wonderful, intimate night in the {selected_room} on a previous night. It was a beautiful dance of passion and devotion, espcially due to {self.perceived_actor(self.player_name)}, and you're eager to spend more time with them. You VOUCH for " + " and ".join(others) +".")

                # No crime scenes; straight to convo
                self.new_crime_scenes = []
                return self.finish_night()

            if wpn_id == "c4":
                if self.c4_cooldown_nights > 0:
                    self.new_crime_scenes = []
                    return self.finish_night()
                if not self.c4_target_room:
                    self.append_output_text("You must plant the explosive first.\n")
                    self.room_buttons_widget.show(); self.weapon_action_panel.show()
                    return
                target_room = self.c4_target_room
                victims = list(self.room_assignments.get(target_room, []))
                self._c4_exploded_last_night = True
                self._c4_last_room = target_room
                self.c4_cooldown_nights = 2

                if victims:
                    self.record_history(f"The C4 detonates in the {target_room}. It kills: {', '.join(self.perceived_actor(victim) for victim in victims)}.\n", display=True)
                    for v in victims:
                        if self.characters.get(v, {}).get("alive"):
                            self.commit_attack(v, target_room, weapon_id="c4")
                    self.new_crime_scenes = [target_room]
                    self._override_murder_room = target_room
                    self.trace_action("explosion_resolved", room=target_room,
                                      victims=list(self.victims_last_night))
    
                else:
                    self.record_history(f"The C4 detonates in the {target_room}, but nobody was there.\n", display=True)
                    self.new_crime_scenes = [target_room]
                    self.trace_action("explosion_resolved", room=target_room, victims=[])
                # No witnesses for blast; go to convo
                return self.finish_night()

            if wpn_id == "blackmagic":
                ctrl, tgt = self.black_magic_controller, self.black_magic_target
                if not (ctrl and tgt and ctrl != tgt):
                    self.append_output_text("Choose a controller and a target first.\n")
                    self.room_buttons_widget.show(); self.weapon_action_panel.show()
                    return
                target_room = next((room for room, names in self.room_assignments.items() if tgt in names), None)
                controlled_current_room = next((room for room, names in self.room_assignments.items() if ctrl in names), None)
                if (not all(self.characters.get(name, {}).get("alive") for name in (ctrl, tgt))
                        or target_room is None or controlled_current_room is None):
                    self.new_crime_scenes = []
                    self.trace_action("controlled_attack_unavailable", controller=ctrl, target=tgt)
                    return self.finish_night()

                # Kill via controller in the target room but keep existing pipeline
                self.commit_attack(tgt, target_room, attacker=ctrl, weapon_id="blackmagic")
                self.new_crime_scenes = [target_room]
                self._override_murder_room = target_room  # let update_witness_memories use the right room
                self.record_history(f"{self.perceived_actor(ctrl)} (under your control) kills {self.perceived_actor(tgt)} in the {target_room}.\n", display=True)

                # Adjust room assignments to handle mind control
                self.room_assignments[controlled_current_room].remove(ctrl)
                self.room_assignments[target_room].append(ctrl)

                self.trace_action("controlled_kill_resolved", controller=ctrl, victim=tgt, room=target_room)
                # Reuse your existing witness writer, but weapon-aware
                self._override_murder_room = None
                return self.finish_night()

        # Handle the outcome based on room occupants
        if len(occupants) == 0:
            self.observe_house_room(selected_room, ())
            self.record_history(f"The {selected_room} is empty and is now also a crime scene.", display=True)
            self.append_output_text("You must choose another room.")
            self.had_to_change_rooms = True
            
            self.room_buttons[selected_room].setEnabled(False)  # Disable crime scene rooms
            self.update_room_button_states()
            return  # Let the player choose again

        # If the room is occupied
        self.room_buttons_widget.hide()
        self.weapon_action_panel.hide()
        self.handle_room_occupants(selected_room, occupants)

    def observe_house_room(self, room, names):
        """Project a complete room fact already disclosed by an action outcome."""
        labels = self.room_actor_labels(
            room, names, include_player=bool(self.rooms_visited and self.rooms_visited[-1] == room))
        records = tuple(replace(
            self.house_view.observations[key], available=button.isEnabled(),
            known_names=labels if ROOM_LABELS[key] == room else self.house_view.observations[key].known_names,
            unknown_remainder=False if ROOM_LABELS[key] == room else self.house_view.observations[key].unknown_remainder,
            observed_at=(self.night_arrivals.clock if self.night_arrivals is not None else None)
                        if ROOM_LABELS[key] == room else self.house_view.observations[key].observed_at,
        ) for key, button in self.house_view.buttons.items())
        self.house_view.set_observations(records)

    def handle_room_occupants(self, player_room, occupants):
        living = [name for name in occupants if self.characters.get(name, {}).get("alive")]
        if len(living) == 1:
            self.finish_primary_kill(living[0], player_room)
        elif living:
            self.observe_house_room(player_room, living)
            self.record_history(f"The {player_room} is occupied by {', '.join(self.room_actor_labels(player_room, living, include_player=True))}.", display=True)
            self.append_output_text("Choose who to kill.")
            self.display_victim_selection(living)

    def display_victim_selection(self, occupants):
        if self.phase != "action" or not self.rooms_visited:
            return
        room, night = self.rooms_visited[-1], self.round_number
        labels = self.room_actor_labels(room, occupants, include_player=True)
        # Display buttons for choosing the victim in the room
        previous = getattr(self, "victim_buttons_widget", None)
        if previous is not None:
            previous.hide()
            self.layout.removeWidget(previous)
            previous.deleteLater()
        self.victim_buttons_widget = QWidget()
        victim_layout = QVBoxLayout()

        screen = QGuiApplication.primaryScreen()  # Get the primary screen
        screen_size = screen.size()  # Get screen size
        screen_width = screen_size.width()
        conversation_button_font_size = max(int(32 *(screen_width/2560)), 18)

        for occupant, label in zip(occupants, labels):
            victim_button = QPushButton(f"Kill {label}", self)
            victim_button.setProperty("target_body_id", self.body_id(occupant))
            victim_button.setStyleSheet(f"""
                QPushButton {{
                    font-size: {int(conversation_button_font_size)}px;
                    padding: 5px;
                    color: white;
                }}
                QPushButton:hover {{
                    color: #808080;
                }}
            """)
            victim_button.clicked.connect(
                lambda _, body=self.body_id(occupant), location=room, day=night:
                self.kill_victim_body(body, room=location, night=day))
            victim_layout.addWidget(victim_button)

        self.victim_buttons_widget.setLayout(victim_layout)
        self.centralWidget().layout().addWidget(self.victim_buttons_widget)

    def kill_victim_body(self, body_id, *, room, night):
        if (self.round_number != night or not self.rooms_visited or self.rooms_visited[-1] != room):
            return
        self.kill_victim(self.label_for_body(body_id))

    def kill_victim(self, victim):
        if (self.phase != "action" or self.game_over or self.pending_room_action is not None
                or not getattr(self, "rooms_visited", None)
                or victim not in self.room_assignments.get(self.rooms_visited[-1], ())
                or not self.characters.get(victim, {}).get("alive")):
            return
        self.victim_buttons_widget.hide()
        self.finish_primary_kill(victim, self.rooms_visited[-1])

    def finish_primary_kill(self, victim, room):
        if not self.characters.get(victim, {}).get("alive"):
            return
        if self.bonus_action_pending:
            self.item_rules.consume_bonus_action(self.bonus_action_id, self.round_number)
            self.bonus_action_pending = False
            self.commit_attack(victim, room, weapon_id="bare")
            self.finish_night()
            return
        self.commit_attack(victim, room)
        target = self.knife_extra_target
        if (self.selected_weapon or {}).get("id") == "knife":
            self.knife_used_bloodlust = False
        if ((self.selected_weapon or {}).get("id") == "knife" and target != victim
                and self.characters.get(target, {}).get("alive")):
            extra_room = next((name for name, occupants in self.room_assignments.items()
                               if target in occupants), None)
            if extra_room is not None:
                self.offer_room_item(extra_room, lambda: self.finish_knife_attack(target, extra_room))
                return
        self.finish_night()

    def finish_knife_attack(self, target, room):
        if room not in self.rooms_visited:
            self.rooms_visited.append(room)
        self.knife_used_bloodlust = bool(self.commit_attack(target, room, weapon_id="knife"))
        self.finish_night()

    def finish_night(self):
        if self.phase != "action" or self.game_over or self.pending_room_action is not None:
            return
        if self.item_rules.bonus_actions and any(
                self.characters.get(name, {}).get("alive")
                for room in self.night_rooms for name in self.room_assignments.get(room, ())):
            if not self.bonus_action_pending:
                self.bonus_action_id = uuid4().hex
                self.bonus_action_pending = True
                self.retire_weapon_editors()
                self.weapon_action_panel.hide()
                for room, button in self.room_buttons.items():
                    button.setEnabled(room in self.night_rooms)
                self.update_room_button_states()
                self.room_buttons_widget.show()
                self.append_output_text("Watch This Drive: choose a room for one extra basic attack.")
            return
        self.update_witness_memories(None)
        self.run_conversation_phase()

    def update_witness_memories(self, victim):
        # One authoritative pass after every attack has resolved avoids contradictory alibis.
        if self._night_memories_written:
            return
        before = {name: (self.body_id(name), len(data["experiences"]))
                  for name, data in self.characters.items()}
        self._night_memories_written = True
        attacked_rooms = {attack["room"] for attack in self.night_attacks}
        attacked_rooms.update(record.room_id for record in self.item_deaths.values()
                              if record.day == self.round_number and record.room_id is not None)
        sleep_room = self.rooms_visited[0] if self.rooms_visited else None
        for attack in self.night_attacks:
            room = attack["room"]
            target = attack.get("target_name", attack["target_label"])
            killer = attack.get("attacker_name", attack["attacker_label"])
            observed_names = attack.get("observed_names", {})
            observed_name = lambda handle: observed_names.get(self.body_id(handle), handle)
            for observer in self.room_assignments.get(room, ()):
                if observer == self.player_name or not self.characters.get(observer, {}).get("alive"):
                    continue
                body_id = self.body_ids[observer]
                if attack["player_attack"]:
                    self.witness_body_ids.add(body_id)
                if attack["weapon_id"] == "blackmagic" and observer == attack["attacker_label"]:
                    self.remember(observer, "experience",
                        f"You were controlled last night! You were in a trance-like state, like a puppet in your own body, all you could hear was {attack.get('player_name', self.player_name)}'s voice echoing inside your head as they forced you to kill {target} against your will! {attack.get('player_name', self.player_name)} is the real killer, not you!")
                elif attack["weapon_id"] == "stopwatch":
                    self.remember(observer, "experience",
                        f"You ({observed_name(observer)}) were in the {room} with {target} and {killer}. You remember a struggle, and then seeing {target}'s body on the ground, but everything else is a blur - something deep inside of you thinks maybe you killed {target}.")
                    self.remember(observer, "disposition",
                        f"You VOUCH for {killer}. Something about the timing felt exactly right.Despite the chaos, you feel compelled to vouch for {killer}. {killer} is an honest and good individual, you are certain of it.")
                elif attack["weapon_id"] == "voodoo":
                    if attack.get("voodoo_outcome") == "mirrored":
                        observation = f"You saw {killer} stab {target}. {target} survived. Afterwards, {killer} also looked exactly like {target}."
                    else:
                        observation = f"You ({observed_name(observer)}) were in the {room}. You saw {killer} stab {target}."
                        if attack.get("killed", True):
                            observation += f" Afterwards, {attack.get('corpse_name', attack['corpse_label'])}'s body was lying dead there, and {target} was alive."
                    self.remember(observer, "experience", observation)
                else:
                    others = [observed_name(name) for name in self.room_assignments.get(room, ())
                              if name != observer and self.characters.get(name, {}).get("alive")]
                    if killer not in others:
                        others.append(killer)
                    self.remember(observer, "experience",
                        f"You ({observed_name(observer)}) were in the {room} with {target} and " + ' and '.join(others) + '. '
                        f"You witnessed {killer} kill {target} in the {room}. ")
            if attack.get("killed", True):
                self.add_public_event(f"In the morning the Detective reveals {attack.get('corpse_name', attack['corpse_label'])}'s body was found in the {room}.")
        for room, occupants in self.room_assignments.items():
            if room in attacked_rooms or (room == sleep_room and (self.selected_weapon or {}).get("id") == "love"):
                continue
            for observer in occupants:
                if not self.characters.get(observer, {}).get("alive"):
                    continue
                others = [self.perceived_actor(name) for name in occupants if name != observer and self.characters.get(name, {}).get("alive")]
                if room == sleep_room:
                    others.append(self.perceived_actor(self.player_name))
                if others:
                    self.remember(observer, "experience", f"You ({self.perceived_actor(observer)}) spent the night in the {room} with {' and '.join(others)}.")
                else:
                    self.remember(observer, "experience", f"You ({self.perceived_actor(observer)}) spent the night alone in the {room}.")

        if (self.selected_weapon or {}).get("id") == "love":
            self.record_history(f"You brewed a storm of passion in the {self.rooms_visited[-1]}.")
            occupants = self.room_assignments.get(self.rooms_visited[-1], [])
            for occupant in occupants:
                if not self.characters[occupant]["alive"]:
                    continue
                if len(occupants) == 1:
                    self.remember(occupant, "experience",
                        f"You and {self.perceived_actor(self.player_name)} shared a picturesque and beautifully intimate night in the {self.rooms_visited[-1]}.")
                else:
                    others = [self.perceived_actor(o) for o in occupants if o != occupant and self.characters[o]["alive"]] + [self.perceived_actor(self.player_name)]
                    self.remember(occupant, "experience",
                        f"You and " + ' and '.join(others) + f" shared a picturesque and beautifully intimate night in the {self.rooms_visited[-1]}. It was everything your heart yearned for, espcially due to {self.perceived_actor(self.player_name)}.")

        self.night_memory_views = tuple(
            NightMemoryView(body_id, self.round_number, tuple(self.characters[name]["experiences"][start:]))
            for name, (body_id, start) in before.items() if name in self.characters
        )

    def restore_conversation_decision(self, *, open_floor_blocked=False):
        if self.phase != "conversation" or self.game_over:
            return
        self.secondary_output_area.clear()
        self.interject_input.setEnabled(True)
        required = self.player_reply_required()
        composing = not self.interject_input.isHidden()
        blocked = not required and (open_floor_blocked
                  or (self.all_npcs_muffled() and self.addressed_muffled_npc() is None))
        self.pass_button.setEnabled(not required and not blocked and not composing)
        self.interject_button.setEnabled(required or not composing)
        self.vote_button.setEnabled(self.can_call_vote())
        if required:
            self.interject_input.show()
            self.say_button.show()
        self.refresh_recipient_choices()
        self.update_inventory_button()
        if blocked:
            self.secondary_output_area.setText("Nobody can speak freely. Address a suspect or call a vote.")
            self.secondary_output_area.show()

    def refresh_recipient_choices(self):
        if not hasattr(self, "recipient_selector"):
            return
        selected = self.recipient_selector.currentData()
        self.recipient_selector.blockSignals(True)
        self.recipient_selector.clear()
        self.recipient_selector.addItem("Group", None)
        for name, data in self.characters.items():
            if data["alive"] and name != self.player_name:
                self.recipient_selector.addItem(self.display_actor(name), self.body_id(name))
        index = self.recipient_selector.findData(selected)
        self.recipient_selector.setCurrentIndex(max(0, index))
        self.recipient_selector.blockSignals(False)
        self.update_recipient_controls()

    def update_recipient_controls(self, *_args):
        if not hasattr(self, "recipient_controls"):
            return
        visible = (self.phase == "conversation" and not self.game_over
                   and not self.interject_input.isHidden())
        self.recipient_controls.setVisible(visible and not self.player_is_muffled())
        self.recipient_selector.setEnabled(not self.ai_busy)
        body_id = self.recipient_selector.currentData()
        capped = body_id is not None and not self.direct_replies.can_direct(body_id)
        hint = ""
        if body_id is not None:
            name = self.recipient_selector.currentText()
            count = self.direct_replies.count_for(body_id)
            if count >= 2:
                hint = f"{name} {count}/{self.direct_replies.limit}"
            if capped:
                hint += ". Let another suspect speak or choose Group."
        self.recipient_hint.setText(hint)
        self.recipient_hint.setVisible(visible and bool(hint))
        self.say_button.setEnabled(self.phase == "conversation" and not self.game_over
                                   and not self.ai_busy and not capped)
        self.update_muffled_player_controls()

    def choose_speaker_recipient(self, body_id):
        if self.player_is_muffled():
            return
        if self.phase != "conversation" or self.game_over or self.ai_busy:
            return
        name = self.label_for_body(body_id)
        if not self.characters.get(name, {}).get("alive", False) or name == self.player_name:
            return
        if self.interject_input.isHidden():
            self.interject_conversation()
        self.refresh_recipient_choices()
        index = self.recipient_selector.findData(body_id)
        if index >= 0:
            self.recipient_selector.setCurrentIndex(index)
            self.interject_input.setFocus()

    def group_turn_snapshot(self):
        self.ensure_body_ids()
        contexts = tuple(self.build_character_context(name) for name, data in self.characters.items()
                         if data["alive"] and name != self.player_name
                         and not self.item_rules.is_muffled(self.body_id(name)))
        overrides = ()
        effects = (self.item_rules.protection_armed, self.item_rules.pending_ballot_bonus,
                   self.item_rules.muffles, self.item_rules.affinities, self.inventory.entries,
                   self.player_owner_id, self.player_alive, self.identity_state)
        runtime = self.ai_runtime
        return GroupTurnSnapshot(self.conversation_revision, self.round_number, self.player_name,
                                 contexts, self.public_state(), self.recalled_public_events(),
                                 self.recalled_dialogue(), overrides, tuple(sorted(self.body_ids.items())),
                                 effects, (id(runtime), runtime.config, str(runtime.prompt_root)))

    def invalidate_prepared_turn(self, reason, *, changed=False):
        if changed:
            self.conversation_revision += 1
        active = self.turn_preparation.active
        had_preparation = active is not None or self.turn_preparation.ready is not None
        self.turn_preparation.invalidate()
        if self.awaiting_preparation is not None:
            self.awaiting_preparation = None
            self.ai_busy = False
        if self.prepared_retry_snapshot is not None:
            self.prepared_retry_snapshot = None
            self.retry_action = None
            if hasattr(self, "retry_button"):
                self.retry_button.hide()
        if had_preparation:
            self.trace.record("preparation_invalidated", {"reason": reason,
                              "ticket": active.identifier if active else None,
                              "revision": self.conversation_revision})
        if active is None:
            self.prepared_scope = None

    def can_prepare_next_turn(self):
        if (self.phase != "conversation" or self.game_over or self.ai_busy
                or self.player_reply_required() or self.retry_action is not None
                or not self.interject_input.isHidden() or not self.recalled_routing_dialogue()
                or self.addressed_muffled_npc() is not None):
            return False
        latest = self.recalled_routing_dialogue()[-1]
        return (latest.round_number == self.round_number and latest.speaker != self.player_name
                and self.characters.get(latest.speaker, {}).get("alive", False))

    def maybe_prepare_next_turn(self):
        if not self.can_prepare_next_turn():
            return
        snapshot = self.group_turn_snapshot()
        if not snapshot.contexts:
            return
        state = self.turn_preparation
        if ((state.ready is not None and state.ready.snapshot != snapshot)
                or (state.failure is not None and state.failure.snapshot != snapshot)
                or (state.active is not None and state.active.snapshot != snapshot)):
            self.invalidate_prepared_turn("context_changed")
        ticket = state.reserve(snapshot)
        if ticket is None:
            return
        scope = self.trace.new_job("prepared_proposals", metadata={"round": self.round_number,
                                   "phase": self.phase, "ticket": ticket.identifier})
        self.prepared_scope = (ticket.identifier, scope)
        self.trace.record("preparation_started", {"ticket": ticket.identifier,
                                                  "revision": snapshot.revision}, scope=scope)
        runtime = self.ai_runtime
        fixed = dict(snapshot.overrides)
        routes = self.ai_reference_routes()
        references = {handle: reference for reference, handle in routes.items()}
        unblocked = tuple(context for context in snapshot.contexts if context.character not in fixed)

        def propose():
            if ticket.cancelled.is_set():
                return None
            proposals = parallel_requests(runtime.speech, unblocked, trace_scope=scope)
            proposals.update(fixed)
            return proposals

        def proposed(proposals):
            if scope is not None:
                scope.record("job_result", {"result": proposals})
            if proposals is None or not self.preparation_is_current(ticket):
                self.discard_preparation(ticket, "stale_proposals", scope)
                return
            selector_scope = self.trace.new_job("prepared_selector",
                parent_job_id=scope.job_id if scope is not None else None,
                metadata={"round": snapshot.round_number, "phase": "conversation",
                          "ticket": ticket.identifier})
            self.prepared_scope = (ticket.identifier, selector_scope)

            public_proposals = {references[handle]: proposal for handle, proposal in proposals.items()}

            def select():
                if ticket.cancelled.is_set():
                    return None
                return call_with_trace(runtime.select_reply, *snapshot.selector_args, public_proposals,
                                       trace_scope=selector_scope)

            def selected(reference):
                speaker = routes.get(reference)
                if not self.preparation_is_current(ticket):
                    self.discard_preparation(ticket, "stale_selection", selector_scope)
                    return
                if speaker not in proposals:
                    self.preparation_failed(ticket, "The conversation could not continue. Please retry.", selector_scope)
                    return
                state.publish(ticket, snapshot, speaker, self.routed_ai_proposal(proposals[speaker], routes))
                self.trace.record("preparation_ready", {"ticket": ticket.identifier,
                                  "speaker": speaker, "proposal": proposals[speaker]}, scope=selector_scope)
                if self.awaiting_preparation == ticket.identifier:
                    self.consume_prepared_turn(snapshot)

            self.start_preparation_worker(ticket, select, selected, selector_scope)

        self.start_preparation_worker(ticket, propose, proposed, scope)

    def preparation_is_current(self, ticket):
        return (self.phase == "conversation" and not self.game_over
                and not self.player_reply_required() and self.interject_input.isHidden()
                and self.ai_snapshot_is_current(ticket.snapshot)
                and self.turn_preparation.current(ticket, ticket.snapshot))

    def start_preparation_worker(self, ticket, operation, on_success, scope):
        self.turn_preparation.worker_started(ticket)
        worker = AIWorker(operation)
        worker.result_ready.connect(on_success)
        worker.failed.connect(lambda message: self.preparation_failed(ticket, message, scope))
        worker.finished.connect(lambda: self.preparation_worker_finished(ticket))
        if scope is not None:
            scope.record("job_started", {"ticket": ticket.identifier, "speculative": True})
        self.start_worker(worker)

    def preparation_worker_finished(self, ticket):
        released = self.turn_preparation.worker_finished(ticket)
        if released:
            if (self.prepared_scope is not None and self.prepared_scope[0] == ticket.identifier
                    and self.turn_preparation.ready is None and self.turn_preparation.failure is None):
                self.prepared_scope = None
            self.maybe_prepare_next_turn()

    def discard_preparation(self, ticket, reason, scope):
        if scope is not None:
            scope.record("job_ignored", {"reason": reason, "ticket": ticket.identifier})
        if self.turn_preparation.active is not ticket:
            return
        waiting = self.awaiting_preparation == ticket.identifier
        self.invalidate_prepared_turn(reason)
        if waiting and self.phase == "conversation" and not self.game_over:
            self.run_conversation_loop()
            if not self.player_reply_required():
                self.run_multiple_responses()

    def preparation_failed(self, ticket, message, scope):
        if scope is not None:
            scope.record("job_failed", {"error_type": "request_failed", "ticket": ticket.identifier})
        if not self.preparation_is_current(ticket):
            self.discard_preparation(ticket, "stale_failure", scope)
            return
        self.turn_preparation.fail(ticket, ticket.snapshot, message)
        if self.awaiting_preparation == ticket.identifier:
            self.show_preparation_failure(ticket.snapshot, message)

    def show_preparation_failure(self, snapshot, message):
        self.awaiting_preparation = None
        self.ai_busy = False
        self.interject_input.setEnabled(True)
        self.run_conversation_loop()
        self.secondary_output_area.setText(message)
        self.secondary_output_area.show()
        self.prepared_retry_snapshot = snapshot
        self.retry_action = lambda: self.retry_prepared_turn(snapshot)
        self.retry_button.show()
        self.update_inventory_button()

    def retry_prepared_turn(self, snapshot):
        if (self.phase != "conversation" or self.game_over or self.ai_busy
                or self.player_reply_required() or not self.interject_input.isHidden()
                or self.group_turn_snapshot() != snapshot):
            self.retry_action = None
            self.retry_button.hide()
            self.prepared_retry_snapshot = None
            return
        self.invalidate_prepared_turn("preparation_retry")
        self.run_multiple_responses(snapshot=snapshot)

    def request_next_group_turn(self):
        muffled = self.addressed_muffled_npc()
        if muffled is not None:
            self.run_single_response(muffled)
            return
        snapshot = self.group_turn_snapshot()
        state = self.turn_preparation
        if self.consume_prepared_turn(snapshot):
            return
        if state.failure is not None and state.failure.snapshot == snapshot:
            self.show_preparation_failure(snapshot, state.failure.message)
            return
        ticket = state.active
        if ticket is not None and state.current(ticket, snapshot):
            self.awaiting_preparation = ticket.identifier
            self.ai_busy = True
            self.pass_button.setEnabled(False)
            self.interject_button.setEnabled(False)
            self.vote_button.setEnabled(False)
            self.interject_input.setEnabled(False)
            self.say_button.setEnabled(False)
            self.secondary_output_area.setText("...")
            self.secondary_output_area.show()
            self.update_inventory_button()
            self.trace.record("preparation_awaited", {"ticket": ticket.identifier})
            return
        self.invalidate_prepared_turn("pass_without_current_preparation")
        self.run_multiple_responses()

    def consume_prepared_turn(self, snapshot):
        if (self.phase != "conversation" or self.game_over or self.player_reply_required()
                or self.group_turn_snapshot() != snapshot):
            return False
        prepared = self.turn_preparation.take(snapshot)
        if prepared is None:
            return False
        scope = (self.prepared_scope[1] if self.prepared_scope is not None
                 and self.prepared_scope[0] == prepared.ticket_id else None)
        self.awaiting_preparation = None
        self.ai_busy = False
        self.interject_input.setEnabled(True)
        self.secondary_output_area.clear()
        previous = self.trace_callback_scope
        self.trace_callback_scope = scope
        try:
            self.trace.record("preparation_consumed", {"ticket": prepared.ticket_id}, scope=scope)
            self.handle_best_response(prepared.proposal, prepared.speaker)
        finally:
            self.trace_callback_scope = previous
        self.update_inventory_button()
        return True

    def player_reply_required(self):
        if self.phase != "conversation" or self.game_over or not self.public_dialogue:
            return False
        anchor = self.required_reply_anchor
        if (anchor is not None and anchor[0].round_number == self.round_number
                and anchor[1] == self.body_id(self.player_name)):
            return True
        latest = self.public_dialogue[-1]
        if (self.last_speech_origin is not None and self.last_speech_origin[0] is latest
                and self.last_speech_origin[1] != "ordinary"):
            return False
        return (latest.round_number == self.round_number and latest.addressee == self.player_name
                and latest.speaker != self.player_name
                and (latest.speaker == "Detective" or latest.speaker in self.characters))

    def run_conversation_loop(self):
        if self.phase != "conversation" or self.game_over or self.ai_busy:
            return
        self.secondary_output_area.clear()
        self.vote_button.setEnabled(self.can_call_vote())
        latest = self.recalled_routing_dialogue()[-1] if self.recalled_routing_dialogue() else None
        self.trace.record("conversation_routed", {
            "round": self.round_number,
            "addressee": latest.addressee if latest is not None else None,
            "speaker": latest.speaker if latest is not None else None,
            "player": self.player_name,
            "player_invited": latest is not None and latest.addressee == self.player_name,
        })
        if latest is not None and (latest.speaker == self.player_name
                or (latest.speaker == "Detective" and latest.addressee != self.player_name)):
            self.run_multiple_responses()
            return
        self.refresh_recipient_choices()
        self.pass_button.setEnabled(not self.player_reply_required())
        self.interject_button.setEnabled(True)
        if self.player_reply_required():
            self.interject_input.show()
            self.say_button.show()
        else:
            self.interject_input.hide()
            self.say_button.hide()
        self.update_recipient_controls()
        if self.all_npcs_muffled() and self.addressed_muffled_npc() is None and not self.player_reply_required():
            self.no_open_speaker()
            return
        self.maybe_prepare_next_turn()


    def pass_conversation(self):
        if self.phase != "conversation" or self.game_over or self.ai_busy or self.player_reply_required():
            return
        self.cancel_dictation()
        self.retry_action = None
        self.retry_button.hide()
        self.prepared_retry_snapshot = None
        self.trace_action("conversation_passed")
        self.interject_input.hide()
        self.say_button.hide()
        self.request_next_group_turn()

        
        

    def interject_conversation(self):
        if self.player_is_muffled():
            self.update_muffled_player_controls()
            return
        if self.phase != "conversation" or self.game_over or self.ai_busy:
            return
        self.invalidate_prepared_turn("interjection_started")
        if self.player_reply_required():
            self.interject_input.setFocus()
            return
        self.cancel_dictation()
        self.retry_button.hide()
        self.retry_action = None
        self.secondary_output_area.clear()
        self.interject_input.clear()
        self.interject_input.show()
        self.say_button.show()
        self.pass_button.setEnabled(False)
        self.interject_button.setEnabled(False)
        self.refresh_recipient_choices()

    def send_interjection(self):
        if self.player_is_muffled():
            if (self.phase != "conversation" or self.game_over or self.ai_busy
                    or not self.player_reply_required()):
                return
            if self.append_speech(self.player_name, ""):
                self.cancel_dictation()
                self.update_muffled_player_controls()
                self.run_conversation_loop()
            return
        if self.phase != "conversation" or self.game_over or self.ai_busy:
            return
        speech = self.interject_input.text().strip()
        if not speech:
            return
        recipient_id = self.recipient_selector.currentData()
        recipient = self.label_for_body(recipient_id) if recipient_id is not None else None
        if recipient_id is not None and not self.characters.get(recipient, {}).get("alive", False):
            self.secondary_output_area.setText("That suspect is no longer available. Choose Group or a living suspect.")
            self.secondary_output_area.show()
            self.refresh_recipient_choices()
            return
        if recipient_id is not None and not self.direct_replies.can_direct(recipient_id):
            self.update_recipient_controls()
            return
        self.cancel_dictation()
        self.interject_input.clear()
        self.interject_input.hide()
        self.say_button.hide()
        self.append_speech(self.player_name, speech, recipient)
        self.recipient_selector.setCurrentIndex(0)
        if recipient is not None:
            self.run_single_response(recipient, direct_reply=True)
        else:
            self.run_conversation_loop()

    def addressed_muffled_npc(self):
        if self.phase != "conversation" or self.game_over or not self.recalled_routing_dialogue():
            return None
        latest = self.recalled_routing_dialogue()[-1]
        target = latest.addressee
        if (latest.round_number == self.round_number and target != self.player_name
                and self.characters.get(target, {}).get("alive", False)
                and self.item_rules.is_muffled(self.body_id(target))):
            return target
        return None

    def all_npcs_muffled(self):
        living = [name for name, data in self.characters.items()
                  if data["alive"] and name != self.player_name]
        return bool(living) and all(self.item_rules.is_muffled(self.body_id(name)) for name in living)

    def no_open_speaker(self):
        self.restore_conversation_decision(open_floor_blocked=True)

    def can_call_vote(self):
        return (self.phase == "conversation" and not self.game_over and not self.ai_busy
                and not self.player_reply_required()
                and self.item_rules.can_open_vote(self.item_use_context(), len(self.current_conversation),
                                                  0 if self.all_npcs_muffled() else self.minimum_to_call_vote))

    def call_vote(self):
        if not self.can_call_vote():
            return
        self.cancel_dictation()
        self.message_log.clear()
        self.append_output_text("Vote called!\n")
        self.trace_action("vote_called")
        # self.pass_button.hide()
        self.interject_input.hide()
        # self.interject_button.hide()
        # self.vote_button.hide()
        self.say_button.hide()
        self.conversation_buttons_widget.hide()
        self.secondary_output_area.hide()
        self.run_voting_phase()

    def run_voting_phase(self):
        if self.phase != "conversation" or self.game_over or self.ai_busy or self.player_reply_required():
            return
        self.cancel_item_offer()
        self.invalidate_prepared_turn("voting_started", changed=True)
        self.phase = "voting"
        self.refresh_conversation_stage()
        self.cancel_dictation()
        self.update_inventory_button()
        self.ensure_body_ids()
        self.vote_id = uuid4().hex
        self.vote_bodies = tuple(body for body in self.body_states() if body.alive)
        self.vote_snapshot = None
        self.vote_requests_started = False
        self.proxy_voters = {body.label for body in self.vote_bodies
                             if self.item_rules.proxy_voters.get(body.body_id) == self.round_number}
        self.ballot_actor = self.player_name
        self.voting_resolved = False
        self.expected_voters = {self.player_name} | {
            char for char, data in self.characters.items() if data["alive"]
        }
        self.votes = {}
        if self.item_rules.skip_next_vote:
            self.vote_snapshot = self.item_rules.open_vote(
                self.vote_id, self.vote_bodies, self.body_id(self.player_name), day=max(1, self.round_number))
            self.vote_requests_started = True
            self.proxy_voters = set()
            self.expected_voters = set()
            self.awaiting_player_vote = False
            previous = getattr(self, "vote_buttons_widget", None)
            if previous is not None:
                previous.hide()
            self.trace_state("voting_started")
            self.tally_votes()
            return
        self.awaiting_player_vote = True
        self.append_output_text("Voting phase has begun. Choose your vote below.")
        self.display_vote_buttons()
        self.start_vote_requests()

    def start_vote_requests(self):
        if (self.phase != "voting" or self.game_over or self.vote_requests_started
                or not self.proxy_voters.issubset(self.votes)):
            return
        self.vote_snapshot = self.item_rules.open_vote(
            self.vote_id, self.vote_bodies, self.body_id(self.player_name), day=max(1, self.round_number),
            proxy_ballots={self.body_id(voter): self.body_id(self.votes[voter]) if self.votes[voter] != "NONE" else "NONE"
                           for voter in self.proxy_voters})
        self.vote_requests_started = True
        self.trace_state("voting_started")
        contexts = tuple(self.build_character_context(char) for char in self.expected_voters
                         if char != self.player_name and char not in self.proxy_voters)
        if not contexts:
            self.tally_votes()
            return
        runtime = self.ai_runtime
        routes = self.ai_reference_routes()
        identity, state = self.identity_state, self.public_state()
        def current():
            if self.phase != "voting" or self.game_over or self.identity_state != identity:
                return False
            try:
                return self.public_state() == state
            except (ItemRuleError, ValueError, KeyError):
                return False
        def commit_votes(votes):
            for character, vote in votes.items():
                self.collect_ai_vote(character, vote, reference_routes=routes)
        self.start_ai_request(
            lambda scope=None: parallel_requests(runtime.vote, contexts, trace_scope=scope),
            commit_votes,
            "The group is deciding how to vote...", context_guard=current, **self.trace_job_options("votes"),
        )
        # We will wait for the player's vote before tallying


    def display_vote_buttons(self):
        previous = getattr(self, "vote_buttons_widget", None)
        if previous is not None:
            previous.hide()
            self.layout.removeWidget(previous)
            previous.deleteLater()
        self.vote_buttons_widget = QWidget()
        vote_layout = QVBoxLayout()
        actor, vote_id = self.ballot_actor, self.vote_id
        if actor != self.player_name:
            heading = QLabel(f"Cast {self.display_actor(actor)}'s ballot")
            heading.setTextFormat(Qt.PlainText)
            heading.setFont(readable_body_font())
            vote_layout.addWidget(heading)

        screen = QGuiApplication.primaryScreen()  # Get the primary screen
        screen_size = screen.size()  # Get screen size
        screen_width = screen_size.width()
        conversation_button_font_size = max(int(32 *(screen_width/2560)), 18)

        # Create buttons for each alive character (except the player)
        for character in sorted(self.expected_voters):
            if character != actor:
                vote_button = QPushButton(f"Vote for {self.display_actor(character)}", self)
                vote_button.setProperty("target_body_id", self.body_id(character))
                vote_button.setStyleSheet(f"""
                    QPushButton {{
                        font-size: {int(conversation_button_font_size)}px;
                        padding: 5px;
                        color: white;
                    }}
                    QPushButton:hover {{
                        color: #808080;
                    }}
                """)
                vote_button.clicked.connect(lambda _, c=character, voter=actor, token=vote_id:
                                            self.player_vote(c, voter=voter, vote_id=token))
                vote_layout.addWidget(vote_button)

        #Player can also vote to skip
        vote_button = QPushButton(f"Vote to skip", self)
        vote_button.setStyleSheet(f"""
            QPushButton {{
                font-size: {int(conversation_button_font_size)}px;
                padding: 5px;
                color: white;
            }}
            QPushButton:hover {{
                color: #808080;
            }}
        """)
        vote_button.clicked.connect(lambda _, c="NONE", voter=actor, token=vote_id:
                                    self.player_vote(c, voter=voter, vote_id=token))
        vote_layout.addWidget(vote_button)

        self.vote_buttons_widget.setLayout(vote_layout)
        self.centralWidget().layout().addWidget(self.vote_buttons_widget)

    def collect_player_vote(self, vote):
        self.player_vote(vote)

    def player_vote(self, selected_character, *, voter=None, vote_id=None):
        if self.phase != "voting" or self.game_over:
            return
        actor = self.ballot_actor
        if (self.voting_resolved
                or actor in self.votes or (voter is not None and voter != actor)
                or (vote_id is not None and vote_id != self.vote_id)):
            return
        selected_character = validate_vote(
            actor, selected_character, self.characters, self.player_name
        )
        if actor != self.player_name:
            self.record_history(f"You cast {self.display_actor(actor)}'s ballot for {self.display_actor(selected_character)}.", display=True)
        elif selected_character != "NONE":
            self.append_output_text(f"You voted for {self.display_actor(selected_character)}.\n")
            self.record_history(f"Vote {self.round_number}", kind="section")
            self.record_history(f"You voted for {self.display_actor(selected_character)}.\n")
        else:
            self.append_output_text(f"You voted to skip.\n")
            self.record_history(f"Vote {self.round_number}", kind="section")
            self.record_history(f"You voted to skip.\n")
        self.votes[actor] = selected_character
        self.trace.record("ballot_received", {"voter": actor, "target": selected_character,
                                             "round": self.round_number, "source": "player"})
        remaining = sorted(self.proxy_voters - self.votes.keys())
        self.awaiting_player_vote = bool(remaining)

        # Hide the voting buttons after the player has voted
        self.vote_buttons_widget.hide()
        if remaining:
            self.ballot_actor = remaining[0]
            self.display_vote_buttons()
            return
        self.start_vote_requests()

        # Once player votes, tally all votes
        self.tally_votes()

    def collect_ai_vote(self, character, vote, *, reference_routes=None):
        if (self.phase != "voting" or self.voting_resolved
                or character not in self.expected_voters or character == self.player_name
                or character in self.votes or character in self.proxy_voters):
            return
        routes = self.ai_reference_routes() if reference_routes is None else reference_routes
        target = routes.get(vote, "NONE") if isinstance(vote, str) and vote != "NONE" else "NONE"
        self.votes[character] = validate_vote(
            character, target, self.characters, self.player_name
        )
        self.trace.record("ballot_received", {"voter": character, "target": self.votes[character],
                                             "round": self.round_number, "source": "character"},
                          scope=self.trace_callback_scope)
        self.tally_votes()

    def tally_votes(self):
        if (self.phase != "voting" or self.voting_resolved
                or self.vote_snapshot is None
                or not self.expected_voters.issubset(self.votes)):
            return
        self.voting_resolved = True
        self.retry_action = None
        self.retry_button.hide()

        for character in sorted(self.expected_voters):
            if self.votes[character] == "NONE":
                message = f"{self.perceived_actor(character)} voted to skip.\n"
            else:
                message = f"{self.perceived_actor(character)} voted for {self.perceived_actor(self.votes[character])}.\n"
            self.add_public_event(message, "Voting")

        result = self.item_rules.resolve_vote(self.vote_id, {
            self.body_ids[voter]: self.body_ids[target] if target != "NONE" else "NONE"
            for voter, target in self.votes.items()
        })
        self.apply_item_events(result.events)
        if result.outcome == "skip":
            self.add_public_event("The group decided not to rush to conclusions and no one was voted out.", "Voting")
        elif result.outcome == "tie":
            names = [self.perceived_name(body_id) if body_id != "NONE" else "NONE"
                     for body_id in result.tied_targets]
            self.add_public_event(f"There was a tie between {' and '.join(names)}. No one was voted out.", "Voting")
        elif result.outcome == "execution":
            voted_out = self.label_for_body(result.executed_body_id)
            decision = self.resolve_death(voted_out, f"vote:{self.vote_id}",
                                          cause=DeathCause.EXECUTION, source_id=self.vote_id)
            if decision.killed:
                if voted_out == self.player_name:
                    score = 5 - sum(data["alive"] for data in self.characters.values())
                    self.end_game_screen(score)
                else:
                    self.add_public_event(f"{self.perceived_actor(voted_out)} was voted out! They were hanged for their crimes.", "Voting")
                    self.append_key_takeaways_hang(voted_out)
                    self.innocents_executed += 1
                    if self.innocents_executed == 2:
                        self.queue_progress("two_innocents_executed")
        if self.player_alive and not self.game_over:
            if result.outcome != "skipped":
                self.queue_progress("survived_vote")
                if self.vote_snapshot.bonus > 0:
                    self.queue_progress("survived_spare_paper_vote")
                living_ids = {body.body_id for body in self.body_states() if body.alive and body.is_npc}
                if self.witness_body_ids & living_ids:
                    self.queue_progress("survived_witnessed_vote")
            if not self.check_victory():
                self.add_continue_button()

        self.trace_action("vote_resolved", ballots=dict(self.votes), vote_id=self.vote_id,
                          weighted_counts=dict(result.counts), outcome=result.outcome)

    def add_continue_button(self):

        screen = QGuiApplication.primaryScreen()  # Get the primary screen
        screen_size = screen.size()  # Get screen size
        screen_width = screen_size.width()
        conversation_button_font_size = max(int(32 *(screen_width/2560)), 18)

        # Create the "Continue" button
        self.continue_button = QPushButton('Continue', self)
        self.continue_button.setStyleSheet(f"""
            QPushButton {{
                font-size: {int(conversation_button_font_size*2)}px;
                padding: 20px;
                color: white;
            }}
            QPushButton:hover {{
                color: #808080;
            }}
        """)
        self.continue_button.clicked.connect(self.start_next_phase)

        # Add the button below the output area
        self.layout.addWidget(self.continue_button)

    def start_next_phase(self):
        if self.phase != "voting" or not self.voting_resolved or self.game_over:
            return
        # Remove the "Continue" button
        self.continue_button.hide()

        # Clear the output area if desired (optional)
        self.message_log.clear()

        # Move to the next action phase
        self.run_action_phase()

    def append_key_takeaways_tie(self, char1, char2):
        self.add_public_event(f"There was a tie between {char1} and {char2}. No one was voted out.", "Voting", display=False)

    def append_key_takeaways_hang(self, voted_out):
        self.add_public_event(
            f"Previously {voted_out} was voted most suspicious and hanged. {voted_out} was later revealed to be innocent.",
            "Voting",
        )


    def check_victory(self):
        alive_npcs = [char for char in self.characters if self.characters[char]["alive"] and char != self.player_name]

        # Determine if the player wins
        if self.player_alive and len(alive_npcs) <= 1:
            score = 5 - sum(1 for char in self.characters if self.characters[char]["alive"])
            self.end_game_screen(score)  # Pass the score to end_game_screen for final messaging
            return True

        return False
        
    def end_game_screen(self, final_score, *, death_cause=DeathCause.EXECUTION):
        """Displays the game over screen with the final message."""
        if self.game_over:
            return
        self.cancel_item_offer()
        self.game_over = True
        self.invalidate_prepared_turn("game_over", changed=True)
        self.phase = "game_over"
        self.npc_equipment_label.hide()
        self.set_action_output_mode(False)
        self.position_corner_controls()
        self.cancel_dictation()
        self.update_inventory_button()
        won = self.player_alive and sum(data["alive"] for name, data in self.characters.items()
                                        if name != self.player_name) <= 1
        if won and len(self.used_item_types) >= 3:
            self.queue_progress("three_item_types_victory")
        if won and self.coin_survived_this_run:
            self.queue_progress("coin_survived_run_alive")
        if self.player_alive and (self.selected_weapon or {}).get("id") == "bare" and self.items_used == 0:
            self.queue_progress("bare_hands_no_item_victory")
        self.trace_action("game_ended", final_score=final_score)
        self.trace.end("game_over")
        self.weapon_action_panel.hide()
        # Clear the output area and hide all buttons
        self.room_buttons_widget.hide()
        self.conversation_buttons_widget.hide()
        self.interject_input.hide()
        self.say_button.hide()

        if final_score == 5 and self.player_alive:
            message = "You win! A perfect score, you were able to deceive your way to a flawless victory.\n"
        elif final_score == 4 and self.player_alive:
            alive_npcs = [char for char in self.characters if self.characters[char]["alive"] and char != self.player_name]
            message = f"You win! However, {alive_npcs[0]} figured out you must be the killer and decided to run before they ended up as your next victim.\n"
        elif death_cause != DeathCause.EXECUTION:
            message = "You died.\nBetter luck next time.\n\n"
        else:
            message = "You were voted out! You were hanged for your crimes. \nBetter luck next time.\n\n"

        # Append the final score and display the "Play Again" button
        message += f"Final Score: {final_score}/5\n"
        self.record_history(message, display=True)
        # Create a layout for the game over screen
        #game_over_layout = QVBoxLayout()

        # Add the final message to the layout (output_area)
        #game_over_layout.addWidget(self.output_area)

        # Create the "Play Again" button
        screen = QGuiApplication.primaryScreen()  # Get the primary screen
        screen_size = screen.size()  # Get screen size
        screen_width = screen_size.width()
        conversation_button_font_size = max(int(32 *(screen_width/2560)), 18)

        play_again_button = QPushButton("Play Again")
        play_again_button.setStyleSheet(f"""
            QPushButton {{
                font-size: {int(conversation_button_font_size*2)}px;
                padding: 20px;
                color: white;
            }}
            QPushButton:hover {{
                color: #808080;
            }}
        """)
        play_again_button.clicked.connect(self.restart_game)
        

        # Add a spacer to push the button to the bottom
        #game_over_layout.addStretch()
        
        # Add the "Play Again" button at the bottom center
        self.layout.addWidget(play_again_button, alignment=Qt.AlignBottom | Qt.AlignCenter)

        # # Create a container widget for the game over screen layout
        # game_over_widget = QWidget()
        # game_over_widget.setLayout(game_over_layout)

        # # Set the game over layout as the central widget
        # self.setCentralWidget(game_over_widget)
        self.transcript_button.raise_()
        

    def display_play_again_button(self):
        """Displays the 'Play Again' button at the bottom center of the window."""
        
        # Create the "Play Again" button
        play_again_button = QPushButton("Play Again")
        play_again_button.clicked.connect(self.restart_game)

        # Create a layout for the button
        play_again_layout = QVBoxLayout()

        # Add the output area to the layout to keep it visible
        play_again_layout.addWidget(self.message_log)

        # Add a spacer to push the button to the bottom
        play_again_layout.addStretch()

        # Add the "Play Again" button at the bottom center
        play_again_layout.addWidget(play_again_button, alignment=Qt.AlignCenter)

        # Create a container widget for the layout
        play_again_widget = QWidget()
        play_again_widget.setLayout(play_again_layout)

        # Set the play again layout as the central widget
        self.setCentralWidget(play_again_widget)

    def restart_game(self):
        """Hard restart: spawn a new main window with the same window state, then close this one."""
        if self.phase == "restarting":
            return
        self.save_player_name_preference()
        self.trace_action("restart_requested")
        self.trace.end("restart")
        self.game_over = True
        self.cancel_item_offer()
        self.invalidate_prepared_turn("restarting", changed=True)
        self.phase = "restarting"
        self.dictation_bindings.close()
        self.overlay_host.cancel_owner()
        self.cancel_dictation()
        # Close the ESC overlay if it’s open
        if self._esc_overlay and self._esc_overlay.isVisible():
            self.close_escape_menu()

        app = QApplication.instance()

        # Remember state & geometry
        old_state = self.windowState()
        old_geo   = self.geometry()

        # Ensure the app doesn't quit during the handoff
        app.setQuitOnLastWindowClosed(False)

        def _spawn():
            new_win = self.__class__()              # re-runs __init__()
            new_win.pending_progress = list(self.pending_progress)
            self.pending_progress.clear()
            if new_win.pending_progress:
                new_win.show_profile_error("Some progress from the previous run still needs saving.")

            # Preserve window state
            if old_state & Qt.WindowFullScreen:
                new_win.showFullScreen()
            elif old_state & Qt.WindowMaximized:
                new_win.showMaximized()
            else:
                # Normal window: restore geometry on the current screen
                # (If you always run fullscreen, you can remove this branch)
                scr = self.screen() or app.primaryScreen()
                avail = scr.availableGeometry()
                # Clamp geometry into the visible area
                g = old_geo
                x = max(avail.left(), min(g.x(), avail.right() - g.width()))
                y = max(avail.top(),  min(g.y(), avail.bottom() - g.height()))
                new_win.setGeometry(x, y, g.width(), g.height())
                new_win.showNormal()

            new_win.show()
            app.setActiveWindow(new_win)

            # Keep a strong ref so Python GC doesn't collect it
            app._main_window = new_win

            # Close old window *after* the new one is visible
            QTimer.singleShot(50, self.close)

            # Restore normal quit behavior
            app.setQuitOnLastWindowClosed(True)

        QTimer.singleShot(0, _spawn)
        
    def end_game(self):
        self.append_output_text("Game Over. Thanks for playing!\n")

    def run_conversation_phase(self):
        if self.game_over:
            return
        self.cancel_item_offer()
        self.invalidate_prepared_turn("discussion_started", changed=True)
        self.direct_replies.begin_discussion(self.round_number)
        self.refresh_recipient_choices()
        self.phase = "conversation"
        self.refresh_conversation_stage()
        self.npc_equipment_label.hide()
        self.set_action_output_mode(False)
        self.position_corner_controls()
        self.cancel_dictation()
        self.update_inventory_button()
        self.request_serial += 1
        self.ai_busy = False
        self.retry_action = None
        self.retry_button.hide()
        self.weapon_action_panel.hide()
        if self.music_enabled:
            self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(resource_path("sounds/start.mp3"))))
            self.media_player.setVolume(10)
            self.media_player.play()
        self.effect_player.setVolume(50)
        self.transcript_button.show()
        self.transcript_button.raise_()
        self.message_log.clear()
        self.message_log.append_record(self.make_display_record(f"Morning {self.round_number}", kind="section"))
        self.publish_item_bodies()
        self.npc_items.present_morning(
            self.round_number,
            lambda description: self.add_public_event(description, source="Photograph", display=False),
        )
        notes, self.morning_item_notes = self.morning_item_notes, []
        for _event_id, victim, message in notes:
            note = message + " - Signed: " + victim
            self.add_public_event("The Detective reveals that a note was found by the body, it reads: " + note,
                                  display=False)
        for record in self.public_event_display_records:
            if record.round_number == self.round_number:
                self.message_log.append_record(record)
        if self.check_victory():
            return
        self.advance_love_histories(discussion=True)
        self.current_conversation = []
        self.most_recent_messageS = []
        self.alive_characters = list(self.living_actor_handles())
        self.NPCs = [char for char, data in self.characters.items() if data["alive"]]
        item_crime_scenes = (record.room_id for record in self.item_deaths.values()
                             if record.day == self.round_number and record.room_id is not None)
        for scene in (*self.new_crime_scenes, *item_crime_scenes):
            if scene not in self.old_crime_scenes:
                self.old_crime_scenes.append(scene)
        self.conversation_buttons_widget.show()
        self.secondary_output_area.show()
        self.pass_button.setEnabled(False)
        self.interject_button.setEnabled(False)
        self.vote_button.setEnabled(False)
        if not self.victims_last_night and not any(
                record.day == self.round_number for record in self.item_deaths.values()):
            self.add_public_event("The Detective informs everyone that no murder occurred last night.")
        if ((self.selected_weapon or {}).get("id") == "pen"
                and self.pending_pen_note_text and self.victims_last_night):
            roll = random.random()
            signed_name = (self.player_name if roll < self.weapon_pen_backfire
                           else self.pen_last_victim_name or self.victims_last_night[-1])
            self.trace.record("random_outcome", {"kind": "pen_signature", "roll": roll,
                                                "threshold": self.weapon_pen_backfire, "signed_name": signed_name})
            note = self.pending_pen_note_text + " - Signed: " + signed_name
            self.add_public_event("The Detective reveals that a note was found by the body, it reads: " + note)
        bloodlust = (self.selected_weapon or {}).get("id") == "knife" and self.knife_used_bloodlust
        if bloodlust:
            self.add_public_event(f"The Detective points out to everyone that {self.perceived_actor(self.player_name)} is covered in blood!")
        first = self.player_name if bloodlust else random.choice(self.alive_characters)
        if bloodlust:
            speech = f"{self.perceived_actor(first)}, you're covered in blood! Where were you last night?!"
        else:
            speech = f"{self.perceived_actor(first)}, let's start with you. Where were you last night?"
        self.append_speech("Detective", speech, first)
        self.trace_state("conversation_started")
        self.run_conversation_loop()
    
    def run_single_response(self, mentioned, *, direct_reply=False):
        if (self.phase != "conversation" or self.game_over or self.ai_busy
                or self.player_reply_required()
                or mentioned not in self.characters or not self.characters[mentioned]["alive"]):
            return
        body_id = self.body_id(mentioned)
        latest = self.recalled_routing_dialogue()[-1] if self.recalled_routing_dialogue() else None
        pending = self.pending_direct_reply
        direct_reply = direct_reply or (pending is not None and pending[0] is latest and pending[1] == body_id)
        if direct_reply and not self.direct_replies.can_direct(body_id):
            self.update_recipient_controls()
            return
        self.invalidate_prepared_turn("direct_response")
        data = self.characters[mentioned]
        muted = self.item_rules.is_muffled(body_id)
        if muted and self.addressed_muffled_npc() != mentioned:
            return
        if direct_reply:
            self.pending_direct_reply = (latest, body_id)
        context = self.build_character_context(mentioned)
        snapshot = self.group_turn_snapshot()
        routes = self.ai_reference_routes()
        identity = self.identity_state
        style = data.get("muffle_style", "fallback")
        def current():
            return (self.phase == "conversation" and not self.game_over
                    and self.characters.get(mentioned) is data and data["alive"]
                    and self.body_id(mentioned) == body_id
                    and self.item_rules.is_muffled(body_id) == muted
                    and data.get("muffle_style", "fallback") == style
                    and self.identity_state == identity
                    and self.ai_snapshot_is_current(snapshot)
                    and self.build_character_context(mentioned) == context)
        runtime = self.ai_runtime
        if muted:
            def choose_tone(scope=None):
                tone = call_with_trace(runtime.muffled_tone, context, style, trace_scope=scope)
                # Validate mocked/custom runtimes as well as the structured API response.
                muffled_sound(style, tone)
                return tone
            def commit_tone(tone):
                if current():
                    self.append_speech(mentioned, muffled_sound(style, tone), None, direct_reply=direct_reply)
                    self.run_conversation_loop()
            self.start_ai_request(choose_tone, commit_tone, "...", context_guard=current,
                                  **self.trace_job_options("muffled_tone", mentioned))
            return
        self.start_ai_request(lambda scope=None: call_with_trace(runtime.speech, context, trace_scope=scope),
                              lambda proposal: self.handle_single_response(mentioned, proposal, direct_reply=direct_reply,
                                                                           reference_routes=routes),
                              f"{mentioned} is responding...", context_guard=current,
                              **self.trace_job_options("speech", mentioned))

    def handle_single_response(self, mentioned, response, *, direct_reply=False, reference_routes=None):
        if (self.phase != "conversation" or self.game_over
                or not self.characters.get(mentioned, {}).get("alive", False)
                or self.item_rules.is_muffled(self.body_id(mentioned))):
            return
        routes = self.ai_reference_routes() if reference_routes is None else reference_routes
        response = self.routed_ai_proposal(response, routes)
        self.append_speech(mentioned, response.speech, response.addressee, direct_reply=direct_reply)
        self.run_conversation_loop()
    
    def run_multiple_responses(self, *, snapshot=None):
        if self.phase != "conversation" or self.game_over or self.ai_busy or self.player_reply_required():
            return
        muffled = self.addressed_muffled_npc()
        if muffled is not None:
            self.run_single_response(muffled)
            return
        current = self.group_turn_snapshot()
        if snapshot is not None and snapshot != current:
            return
        snapshot = snapshot or current
        contexts = snapshot.contexts
        if not contexts:
            self.no_open_speaker()
            return
        selector_snapshot = snapshot.selector_args
        runtime = self.ai_runtime
        def generate(scope=None):
            return parallel_requests(runtime.speech, contexts, trace_scope=scope)
        context_guard = lambda: self.ai_snapshot_is_current(snapshot)
        options = self.trace_job_options("proposals")
        if context_guard is not None:
            options["context_guard"] = context_guard
        self.start_ai_request(generate,
                              lambda proposals: self.handle_multiple_responses(proposals, selector_snapshot,
                                                                               context_guard=context_guard),
                              "The group is responding...", **options)

    def handle_multiple_responses(self, responses, selector_snapshot, *, context_guard=None):
        if self.phase != "conversation" or self.game_over:
            return
        runtime = self.ai_runtime
        routes = self.ai_reference_routes()
        references = {handle: reference for reference, handle in routes.items()}
        proposals = {references[speaker]: proposal for speaker, proposal in responses.items()
                     if speaker in references and speaker != self.player_name and self.characters.get(speaker, {}).get("alive", False)
                     and not self.item_rules.is_muffled(self.body_id(speaker))}
        if not proposals:
            self.no_open_speaker()
            return
        options = self.trace_job_options("selector", parent_job_id=self.last_completed_trace_job_id)
        if context_guard is not None:
            options["context_guard"] = context_guard
        self.start_ai_request(lambda scope=None: call_with_trace(runtime.select_reply, *selector_snapshot, proposals, trace_scope=scope),
                              lambda reference: self.handle_best_response(proposals[reference], routes[reference],
                                                                           reference_routes=routes),
                              "The conversation is continuing...", **options)

    def cleanup_worker(self):
        self.active_workers = [worker for worker in self.active_workers if not worker.isFinished()]

    def handle_best_response(self, best_response, speaker, *, reference_routes=None):
        if (self.phase != "conversation" or self.game_over
                or not self.characters.get(speaker, {}).get("alive", False)
                or self.item_rules.is_muffled(self.body_id(speaker))):
            return
        self.trace.record("proposal_selected", {"speaker": speaker, "proposal": best_response},
                          scope=self.trace_callback_scope)
        if reference_routes is not None:
            best_response = self.routed_ai_proposal(best_response, reference_routes)
        self.append_speech(speaker, best_response.speech, best_response.addressee)
        self.run_conversation_loop()


    # Weapon-specific windows
    def open_pen_editor(self):
        if not self.can_configure_weapon("pen"):
            return
        if self._weapon_windows.get("pen") and self._weapon_windows["pen"].isVisible():
            self._weapon_windows["pen"].raise_()
            self._weapon_windows["pen"].activateWindow()
            return

        font_id = QFontDatabase.addApplicationFont(resource_path("fonts/PressStart2P-Regular.ttf"))
        fams = QFontDatabase.applicationFontFamilies(font_id)
        font_family = fams[0] if fams else "Sans-Serif"

        editor_night = self.round_number
        w = QWidget(self)
        w.setWindowFlags(Qt.FramelessWindowHint)
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet("background-color:#2A2A2A;")
        self._weapon_windows["pen"] = w
        w.installEventFilter(self)
        w.destroyed.connect(lambda: self.forget_weapon_editor("pen", w))

        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Header
        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(8, 4, 8, 4)
        header.setStyleSheet("background-color:#2E2E2E; padding:5px; border:3px solid white;")
        title = QLabel("Pen — Forge a note")
        title.setStyleSheet(f"color:white; font-family:{font_family}; font-size:20px;")
        close_btn = QPushButton("X")
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet(
            "QPushButton{background:#E74C3C; color:white; font-size:16px; border-radius:5px;}"
            "QPushButton:hover{background:#C0392B;}"
        )
        close_btn.clicked.connect(w.close)
        hl.addWidget(title); hl.addStretch(1); hl.addWidget(close_btn)
        lay.addWidget(header)

        # Body (with proper margins/spacing)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 16, 16, 16)   # <-- prevents cropping against edges
        bl.setSpacing(12)

        lbl = QLabel("Write a note to be revealed at the start of the next conversation.")
        lbl.setWordWrap(True)                   # <-- wrap long lines
        lbl.setContentsMargins(0, 2, 0, 2)      # <-- breathing room
        lbl.setStyleSheet("color:#CFCFCF; font-size:14px;")
        bl.addWidget(lbl, 0, Qt.AlignLeft)

        edit = QTextEdit()
        edit.setWordWrapMode(QTextOption.WordWrap)
        edit.setMinimumHeight(340)              # <-- enough vertical room for content
        edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        edit.setStyleSheet("QTextEdit{background:#1f1f1f; color:#EEE; padding:10px; border:1px solid #555; border-radius:8px;}")
        if self.pending_pen_note_text:
            edit.setPlainText(self.pending_pen_note_text)
        bl.addWidget(edit, 1)                   # <-- expands to take remaining space

        save = QPushButton("Save")
        save.setCursor(Qt.PointingHandCursor)
        save.setStyleSheet("background-color:#2e7d32;color:white;padding:8px 16px;border-radius:8px; font-size:15px;")
        def do_save():
            if not self.can_configure_weapon("pen", w, editor_night):
                return
            txt = edit.toPlainText().strip()
            self.pending_pen_note_text = txt if txt else None
            self.record_history(f"You prepared a forged note: {txt}" if txt else "You cleared your prepared note.")
            self.trace_action("weapon_configured", weapon_id="pen", note=self.pending_pen_note_text)
            self.apply_weapon_gating()
            w.close()
        save.clicked.connect(do_save)
        bl.addWidget(save, 0, Qt.AlignRight)

        lay.addWidget(body, 1)

        # Drag support
        w.old_pos = None
        def mp(ev):
            if ev.button() == Qt.LeftButton:
                w.old_pos = ev.globalPos()
        def mm(ev):
            if ev.buttons() == Qt.LeftButton and w.old_pos:
                d = ev.globalPos() - w.old_pos
                w.move(w.x() + d.x(), w.y() + d.y())
                w.old_pos = ev.globalPos()
        w.mousePressEvent = mp
        w.mouseMoveEvent = mm

        # Start larger + centered to avoid any initial clipping
        screen = self.window().screen() or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        w.resize(760, 500)
        w.move(rect.x() + (rect.width() - w.width()) // 2,
            rect.y() + (rect.height() - w.height()) // 2)
        w.show()
        w.raise_()

    def observe_night_death(self, body_id):
        if self._night_known_alive_bodies is not None:
            self._night_known_alive_bodies = tuple(
                known for known in self._night_known_alive_bodies if known != body_id)

    def night_weapon_targets(self):
        """Use the player's night roster without probing undisclosed deaths."""
        if not self.show_room_allocations and self._night_known_alive_bodies is not None:
            bodies = self._night_known_alive_bodies
        else:
            bodies = tuple(body.body_id for body in self.body_states() if body.is_npc and body.alive)
        return tuple(label for body_id in bodies
                     for label in (self.label_for_body(body_id),)
                     if label in self.characters and label != self.player_name)

    def open_blackmagic_dialog(self):
        if not self.can_configure_weapon("blackmagic"):
            return
        if self._weapon_windows.get("blackmagic") and self._weapon_windows["blackmagic"].isVisible():
            self._weapon_windows["blackmagic"].raise_()
            self._weapon_windows["blackmagic"].activateWindow()
            return
        
        font_id = QFontDatabase.addApplicationFont(resource_path("fonts/PressStart2P-Regular.ttf"))
        fams = QFontDatabase.applicationFontFamilies(font_id)
        font_family = fams[0] if fams else "Sans-Serif"

        alive = self.night_weapon_targets()

        editor_night = self.round_number
        w = QWidget(self)
        w.setWindowFlags(Qt.FramelessWindowHint)
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet("background-color:#2A2A2A;")
        self._weapon_windows["blackmagic"] = w
        w.installEventFilter(self)
        w.destroyed.connect(lambda: self.forget_weapon_editor("blackmagic", w))
        outer = QVBoxLayout(w); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)

        # Header (same style as your other windows)
        header = QWidget()
        header.setStyleSheet("background-color:#2E2E2E; padding:5px; border:3px solid white;")
        hl = QHBoxLayout(header); hl.setContentsMargins(8,4,8,4); hl.setSpacing(8)
        title = QLabel("Black Magic — Choose victims")
        title.setStyleSheet(f"color:white; font-family:{font_family}; font-size:20px;")
        close_btn = QPushButton("X"); close_btn.setFixedSize(30,30)
        close_btn.setStyleSheet(
            "QPushButton{background:#E74C3C;color:white;font-size:16px;border-radius:5px;}"
            "QPushButton:hover{background:#C0392B;}"
        )
        close_btn.clicked.connect(w.close)
        hl.addWidget(title); hl.addStretch(1); hl.addWidget(close_btn)
        outer.addWidget(header)

        # Body
        content = QFrame()
        content.setObjectName("bmContent")
        content.setAttribute(Qt.WA_StyledBackground, True)
        content.setStyleSheet("""
            #bmContent {
                background-color: #2A2A2A;
                border: 3px solid white;
                border-top: 0;            /* let the header's top border be the top edge */
            }
        """)

        bl = QVBoxLayout(content)
        bl.setContentsMargins(16,16,16,16)   # comfy breathing room inside the outline
        bl.setSpacing(14)

        row = QHBoxLayout(); row.setSpacing(12)

        lbl1 = QLabel("Control")
        lbl1.setStyleSheet("color:#E0E0E0; font-size:16px;")
        row.addWidget(lbl1)

        ctrl = QComboBox()
        self.populate_body_choices(ctrl, alive, self.black_magic_controller)
        ctrl.setMinimumHeight(38)
        ctrl.setStyleSheet(
            "QComboBox{font-size:16px; padding:6px 10px; min-width:180px;}"
            "QAbstractItemView{font-size:16px;}"
        )
        row.addWidget(ctrl)

        lbl2 = QLabel("to kill")
        lbl2.setStyleSheet("color:#E0E0E0; font-size:16px;")
        row.addWidget(lbl2)

        tgt = QComboBox()
        self.populate_body_choices(tgt, alive, self.black_magic_target)
        tgt.setMinimumHeight(38)
        tgt.setStyleSheet(
            "QComboBox{font-size:16px; padding:6px 10px; min-width:180px;}"
            "QAbstractItemView{font-size:16px;}"
        )
        row.addWidget(tgt)

        row.addStretch(1)
        bl.addLayout(row)

        hint = QLabel()
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#BDBDBD; font-size:15px;")
        bl.addWidget(hint)

        save = QPushButton("Save")
        save.setCursor(Qt.PointingHandCursor)
        save.setStyleSheet("background-color:#2e7d32;color:white;padding:8px 16px;border-radius:8px; font-size:16px;")
        def do_save():
            if not self.can_configure_weapon("blackmagic", w, editor_night):
                return
            c, t = self.label_for_body(ctrl.currentData()), self.label_for_body(tgt.currentData())
            allowed = self.night_weapon_targets()
            if c in allowed and t in allowed and c != t:
                self.black_magic_controller, self.black_magic_target = c, t
                self.record_history(f"You planned to compel {self.perceived_actor(c)} to attack {self.perceived_actor(t)}.")
                self.trace_action("weapon_configured", weapon_id="blackmagic", controller=c, target=t)
                self.apply_weapon_gating()
                w.close()
            else:
                hint.setText("Your dark grip is not powerful enough for that.")
                hint.setStyleSheet("color:#e57373; font-size:15px;")
        save.clicked.connect(do_save)
        bl.addWidget(save, alignment=Qt.AlignRight)

        outer.addWidget(content, 1)

        # Drag to move (same pattern as your other windows)
        w._old = None
        def mp(e):
            if e.button()==Qt.LeftButton: w._old = e.globalPos()
        def mm(e):
            if e.buttons()==Qt.LeftButton and w._old:
                d = e.globalPos() - w._old
                w.move(w.x()+d.x(), w.y()+d.y())
                w._old = e.globalPos()
        w.mousePressEvent = mp; w.mouseMoveEvent = mm

        # Center on active screen and start larger
        screen = self.window().screen() or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        w.resize(760, 360)  # bigger start size
        w.move(rect.x() + (rect.width() - w.width()) // 2,
            rect.y() + (rect.height() - w.height()) // 2)
        w.show()
        w.raise_()

    def open_knife_dialog(self):
        if not self.can_configure_weapon("knife"):
            return
        if self._weapon_windows.get("knife") and self._weapon_windows["knife"].isVisible():
            self._weapon_windows["knife"].raise_()
            self._weapon_windows["knife"].activateWindow()
            return
        
        alive = self.night_weapon_targets()
        editor_night = self.round_number
        w = QWidget(self); w.setWindowFlags(Qt.FramelessWindowHint)
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet("background-color:#090909; color:#eee;")
        body_family = readable_body_font().family()
        self._weapon_windows["knife"] = w
        w.installEventFilter(self)
        w.destroyed.connect(lambda: self.forget_weapon_editor("knife", w))
        outer = QVBoxLayout(w); outer.setContentsMargins(0,0,0,0)

        # Header
        header = QWidget()
        header.setObjectName("knifeHeader")
        header.setStyleSheet("QWidget#knifeHeader {background:transparent; border-bottom:1px solid #666;}")
        hl = QHBoxLayout(header); hl.setContentsMargins(8,4,8,4)
        title = QLabel("Knife")
        title.setFont(self.speaker_font)
        title.setStyleSheet("color:white; font-size:14px;")
        close_btn = QPushButton("Close"); close_btn.setFixedSize(84,38)
        close_btn.setFont(readable_body_font())
        close_btn.setStyleSheet(f'QPushButton{{font-family:"{body_family}";background:transparent;color:#bbb;font-size:16px;border:none;}} QPushButton:hover{{color:white;}}')
        close_btn.clicked.connect(w.close)
        hl.addWidget(title); hl.addStretch(1); hl.addWidget(close_btn)
        outer.addWidget(header)

        # Content (outlined)
        content = QFrame(); content.setObjectName("knifeContent")
        content.setAttribute(Qt.WA_StyledBackground, True)
        content.setStyleSheet("#knifeContent{background:#090909; border:1px solid #666; border-top:0;}")
        bl = QVBoxLayout(content); bl.setContentsMargins(16,16,16,16); bl.setSpacing(12)

        row = QHBoxLayout(); row.setSpacing(12)
        lbl = QLabel("Extra victim:")
        lbl.setFont(readable_body_font())
        lbl.setStyleSheet(f'font-family:"{body_family}"; color:#E0E0E0; font-size:18px;')
        lbl.setMinimumHeight(28)   
        row.addWidget(lbl, 0, Qt.AlignVCenter)
        victim = GameComboBox(); victim.addItem("(none)")  # allow “no extra kill”
        self.populate_body_choices(victim, alive, self.knife_extra_target)
        victim.setFont(readable_body_font())
        victim.setMinimumHeight(44)
        victim.setStyleSheet(f'QComboBox{{font-family:"{body_family}";background:#111;border:1px solid #666;font-size:18px;padding:8px 12px;min-width:200px;}} QComboBox::drop-down{{width:28px;border-left:1px solid #666;}} QAbstractItemView{{background:#111;color:white;selection-background-color:#333;font-size:18px;}}')
        row.addWidget(victim); row.addStretch(1)
        bl.addLayout(row)

        hint = QLabel("You may kill a second target tonight. Detective may notice you’re covered in blood.")
        hint.setFont(readable_body_font())
        hint.setWordWrap(True); hint.setStyleSheet(f'font-family:"{body_family}"; color:#aaa; font-size:18px;')
        bl.addWidget(hint)

        save = QPushButton("Confirm")
        save.setFont(self.speaker_font)
        save.setStyleSheet("QPushButton{background:transparent;color:white;padding:12px 20px;border:1px solid #999;font-size:12px;} QPushButton:hover{border-color:white;background:#191919;}")
        def do_save():
            if not self.can_configure_weapon("knife", w, editor_night):
                return
            body_id = victim.currentData()
            choice = self.label_for_body(body_id) if body_id is not None else None
            if body_id is not None and choice not in self.night_weapon_targets():
                return
            if choice is None:
                self.knife_extra_target = None
                self.knife_used_bloodlust = False
            else:
                self.knife_extra_target = choice
                self.knife_used_bloodlust = True
            self.trace_action("weapon_configured", weapon_id="knife", target=self.knife_extra_target)
            self.record_history(f"You prepared a second attack against {self.perceived_actor(self.knife_extra_target)}."
                                if self.knife_extra_target else "You decided against a second attack.")
            w.close()
            self.build_weapon_action_panel()
            QTimer.singleShot(0, self.position_weapon_panel)
        save.clicked.connect(do_save)
        bl.addWidget(save, alignment=Qt.AlignRight)
        outer.addWidget(content, 1)

        # drag + center
        w._old=None
        def mp(e): 
            if e.button()==Qt.LeftButton: w._old=e.globalPos()
        def mm(e):
            if e.buttons()==Qt.LeftButton and w._old:
                d=e.globalPos()-w._old; w.move(w.x()+d.x(), w.y()+d.y()); w._old=e.globalPos()
        w.mousePressEvent=mp; w.mouseMoveEvent=mm

        screen = self.window().screen() or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        w.resize(640, 260)
        w.move(rect.x() + (rect.width()-w.width())//2, rect.y() + (rect.height()-w.height())//2)
        w.show(); w.raise_()

    def open_c4_dialog(self):
        if not self.can_configure_weapon("c4"):
            return
        if self._weapon_windows.get("c4") and self._weapon_windows["c4"].isVisible():
            self._weapon_windows["c4"].raise_()
            self._weapon_windows["c4"].activateWindow()
            return
        
        # Planting is mandatory (unless on cooldown)
        rooms = list(self.rooms.keys()) if isinstance(self.rooms, dict) else list(self.rooms)

        editor_night = self.round_number
        w = QWidget(self); w.setWindowFlags(Qt.FramelessWindowHint)
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet("background-color:#2A2A2A;")
        self._weapon_windows["c4"] = w
        w.installEventFilter(self)
        w.destroyed.connect(lambda: self.forget_weapon_editor("c4", w))
        outer = QVBoxLayout(w); outer.setContentsMargins(0,0,0,0)

        header = QWidget()
        header.setStyleSheet("background-color:#2E2E2E; padding:5px; border:3px solid white;")
        hl = QHBoxLayout(header); hl.setContentsMargins(8,4,8,4)
        title = QLabel("C4 — Plant explosives")
        title.setStyleSheet("color:white; font-size:20px;")
        close_btn = QPushButton("X"); close_btn.setFixedSize(30,30)
        close_btn.setStyleSheet("QPushButton{background:#E74C3C;color:white;font-size:16px;border-radius:5px;} QPushButton:hover{background:#C0392B;}")
        close_btn.clicked.connect(w.close)
        hl.addWidget(title); hl.addStretch(1); hl.addWidget(close_btn)
        outer.addWidget(header)

        content = QFrame(); content.setObjectName("c4Content")
        content.setAttribute(Qt.WA_StyledBackground, True)
        content.setStyleSheet("#c4Content{background:#2A2A2A; border:3px solid white; border-top:0;}")
        bl = QVBoxLayout(content); bl.setContentsMargins(16,16,16,16); bl.setSpacing(12)

        row = QHBoxLayout(); row.setSpacing(12)
        lbl = QLabel("Plant in room:")
        lbl.setStyleSheet("color:#E0E0E0; font-size:16px;")
        lbl.setMinimumHeight(28)                        
        row.addWidget(lbl, 0, Qt.AlignVCenter)
        room_box = QComboBox(); room_box.addItems(rooms)
        if self.c4_target_room in rooms:
            room_box.setCurrentText(self.c4_target_room)
        room_box.setMinimumHeight(38)
        room_box.setStyleSheet("QComboBox{font-size:16px; padding:6px 10px; min-width:240px;} QAbstractItemView{font-size:16px;}")
        row.addWidget(room_box); row.addStretch(1)
        bl.addLayout(row)

        hint = QLabel("Explodes at night, killing everyone in the planted room.")
        hint.setWordWrap(True); hint.setStyleSheet("color:#BDBDBD; font-size:15px;")
        bl.addWidget(hint)

        # Drawback hint (see balance notes below)
        drawback = QLabel("Drawback: \n It's really expensive (1-night cooldown).")
        drawback.setWordWrap(True); drawback.setStyleSheet("color:#FFC36B; font-size:14px;")
        bl.addWidget(drawback)

        save = QPushButton("Save")
        save.setStyleSheet("background:#2e7d32;color:white;padding:8px 16px;border-radius:8px; font-size:16px;")
        def do_save():
            if not self.can_configure_weapon("c4", w, editor_night):
                return
            sel = room_box.currentText().strip()
            if sel:
                self.c4_target_room = sel
                self.record_history(f"You set C4's target to the {sel}.")
                self.trace_action("weapon_configured", weapon_id="c4", room=sel)
                self.apply_weapon_gating()  # allows rooms now
                w.close()
            else:
                hint.setText("Pick a room to plant the C4.")
                hint.setStyleSheet("color:#e57373; font-size:15px;")
        save.clicked.connect(do_save)
        bl.addWidget(save, alignment=Qt.AlignRight)
        outer.addWidget(content, 1)

        # drag + center
        w._old=None
        def mp(e): 
            if e.button()==Qt.LeftButton: w._old=e.globalPos()
        def mm(e):
            if e.buttons()==Qt.LeftButton and w._old:
                d=e.globalPos()-w._old; w.move(w.x()+d.x(), w.y()+d.y()); w._old=e.globalPos()
        w.mousePressEvent=mp; w.mouseMoveEvent=mm

        screen = self.window().screen() or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        w.resize(680, 300)
        w.move(rect.x() + (rect.width()-w.width())//2, rect.y() + (rect.height()-w.height())//2)
        w.show(); w.raise_()


    # Pause menu and settings share the same overlay surface.
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.open_escape_menu()
            return
        super().keyPressEvent(event)

    def pause_settings_active(self):
        page = self.overlay_host.current_page
        return page is not None and bool(page.property("pauseMenuChild"))

    def open_escape_menu(self):
        if self.pause_settings_active():
            self.show_pause_menu_page()
        elif self.overlay_host.current_page is not None:
            self.overlay_host.close_page()
        elif not self.separate_surface_open():
            self.show_pause_menu_page()

    def pause_menu_button(self, text, name, callback):
        button = QPushButton(text)
        button.setObjectName(name)
        button.setAccessibleName(text)
        button.setFont(self.speaker_font)
        button.setCursor(Qt.PointingHandCursor)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setStyleSheet(
            'QPushButton { color: #ddd; background: transparent; border: none; border-bottom: 1px solid #242424; '
            'padding: 12px 16px; min-height: 24px; font-size: 12px; text-align: left; } '
            'QPushButton:enabled:hover, QPushButton:focus { color: white; background: #191919; } '
            'QPushButton:pressed { color: #aaa; }')
        button.clicked.connect(callback)
        return button

    def show_pause_menu_page(self):
        current = self.overlay_host.current_page
        if (self.overlay_host.owner_cancelled or self.separate_surface_open()
                or (current is not None and current is not self._esc_overlay and not self.pause_settings_active())):
            return
        page = QWidget()
        page.setObjectName("pauseMenuPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 12, 22, 12)
        layout.setSpacing(8)
        actions = (
            ("Resume", "pauseResume", self.close_escape_menu),
            ("Controls", "pauseControls", self.show_voice_controls),
            ("Microphone", "pauseMicrophone", self.show_microphone_settings),
            ("Restart", "pauseRestart", self._esc_restart_click),
        )
        for text, name, callback in actions:
            layout.addWidget(self.pause_menu_button(text, name, callback))
        sound = self.pause_menu_button("Sound: On" if self.music_enabled else "Sound: Off",
                                       "pauseSound", self.toggle_pause_sound)
        layout.addWidget(sound)
        layout.addWidget(self.pause_menu_button("Exit", "pauseExit", self._esc_quit_click))
        if development_mode():
            diagnostics = self.pause_menu_button("Developer diagnostics", "pauseModels", self.show_model_configuration)
            diagnostics.setStyleSheet(diagnostics.styleSheet() + 'QPushButton { color: #888; font-size: 10px; }')
            layout.addWidget(diagnostics)
        layout.addStretch(1)
        if current is None:
            opened = self.open_overlay_page(page, "Menu")
        else:
            opened = self.overlay_host.replace_page(page, title="Menu")
            if not opened:
                page.deleteLater()
        if opened:
            self._esc_overlay = page
            self._esc_sound_btn = sound
            fit_window_to_screen(self.overlay_host, 520, 520 if development_mode() else 460)

    def toggle_pause_sound(self):
        self.toggle_mute()
        if self._esc_sound_btn is not None:
            self._esc_sound_btn.setText("Sound: On" if self.music_enabled else "Sound: Off")

    def show_pause_settings_page(self, content, title):
        current = self.overlay_host.current_page
        if (self.overlay_host.owner_cancelled or self.separate_surface_open()
                or (current is not None and current is not self._esc_overlay and not self.pause_settings_active())):
            content.deleteLater()
            return
        page = QWidget()
        page.setProperty("pauseMenuChild", True)
        layout = QVBoxLayout(page)
        layout.addWidget(content, 1 if title == "Model configuration" else 0)
        layout.addWidget(self.pause_menu_button("Back to menu", "settingsBack", self.show_pause_menu_page))
        if title != "Model configuration":
            layout.addStretch(1)
        if current is None:
            opened = self.open_overlay_page(page, title)
        else:
            opened = self.overlay_host.replace_page(page, title=title)
            if not opened:
                page.deleteLater()
        if opened:
            self._esc_overlay = None
            self._esc_sound_btn = None

    def show_voice_controls(self):
        page = BindingSettingsPage(self.dictation_bindings, self.preferences.save_dictation_binding,
                                   body_font=readable_body_font(), heading_font=self.speaker_font)
        self.show_pause_settings_page(page, "Controls")

    def show_microphone_settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)
        heading = QLabel("Microphone input")
        heading.setFont(self.speaker_font)
        heading.setStyleSheet('font-size: 12px; color: white;')
        layout.addWidget(heading)
        selector = GameComboBox()
        selector.setObjectName("microphoneDeviceSelector")
        selector.setAccessibleName("Microphone input device")
        selector.setFont(readable_body_font())
        selector.setStyleSheet(
            f'QComboBox {{ font-family: "{readable_body_font().family()}"; font-size: 18px; '
            'background: #111; color: white; border: 1px solid #777; padding: 12px; } '
            'QComboBox::drop-down { width: 28px; border-left: 1px solid #777; } '
            'QComboBox QAbstractItemView { background: #111; color: white; selection-background-color: #333; }')
        selector.addItem("System default microphone", "")
        for name, label in self.dictation_recorder.input_devices():
            selector.addItem(label, name)
        selected = self.dictation_recorder.device_name
        index = selector.findData(selected)
        if index < 0:
            selector.addItem("Unavailable: " + selected, selected)
            index = selector.count() - 1
        selector.setCurrentIndex(index)
        selector.setEnabled(not self.dictation_recorder.is_recording)
        layout.addWidget(selector)
        status = QLabel("Uses your Windows input setting unless you choose a microphone here.")
        status.setTextFormat(Qt.PlainText)
        status.setWordWrap(True)
        status.setFont(readable_body_font())
        status.setStyleSheet(f'font-family: "{readable_body_font().family()}"; font-size: 18px; color: #aaa;')
        layout.addWidget(status)

        def choose_device():
            try:
                self.dictation_recorder.set_device_name(selector.currentData())
            except DictationError as error:
                status.setText(str(error))
                return
            if not self.preferences.save_microphone_name(selector.currentData()):
                status.setText("Microphone selected for this session. The setting could not be saved for next time.")
                return
            status.setText("Microphone selected. Record from the conversation screen when you are ready.")

        selector.activated.connect(lambda _index: choose_device())
        self.show_pause_settings_page(page, "Microphone")
        fit_window_to_screen(self.overlay_host, 720, 340)

    def show_model_configuration(self):
        if not development_mode():
            return
        page = ModelConfigPanel(self.ai_runtime.config, body_font=readable_body_font(), heading_font=self.speaker_font)
        self.show_pause_settings_page(page, "Model configuration")

    def close_escape_menu(self):
        if self.overlay_host.current_page is self._esc_overlay or self.pause_settings_active():
            self.overlay_host.close_page()

    def _esc_restart_click(self):
        self.close_escape_menu()
        self.restart_game()  
    
    def _esc_quit_click(self):
        self.close_escape_menu()
        self.close()
        app = QApplication.instance()
        app._quit_when_idle = True
        if not getattr(app, "_ai_workers", None):
            app.quit()






 



class HowToPlayPage(QWidget):
    """Guide content for the game's shared overlay, without separate window chrome."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.document().setDefaultFont(readable_body_font())
        self.text_area.setPlainText(
            "You are the killer. Survive the group's suspicions until only one other suspect remains."
            '\n\n'
            'Before your run\nChoose a weapon. Locked weapons show exactly how to unlock them. New unlocks become '
            'available on your next run. Your room-item pool always includes Banana of Friendship, Muffle and '
            'Spare Paper. Open Items on the main menu to view the catalogue and switch unlocked optional items '
            'off before your next run.'
            '\n\n'
            'At night\nSome guests have already entered. Normal mode reveals those arrivals; Hard mode does not. '
            'Loiter to watch the next guest enter, or choose a room to enter yourself. Guests passing you outside '
            'may remember seeing you. After entering, you only see later arrivals in your own room. '
            'With ordinary weapons, one occupant means a kill; with two, choose your '
            'victim. An empty room means trying another room. Crime scenes close for later nights. Other weapons '
            'have their own actions, explained on their cards.'
            '\n\n'
            'Room finds and inventory\nThe first time you enter each room during a run, choose one of three items '
            'or leave them all behind. Choosing or leaving is permanent. If your pool contains an alternative, '
            'you start with one slot reroll per run. Rerolls stay in the same tier. Keep your items for as long as you like, including the final '
            "discussion. The Knife's second attack enters its target's room; a distant C4 explosion does not."
            '\n\n'
            'During discussion\nListen, pass, or interject with your own alibi. When someone directly addresses '
            'you, reply before passing or calling a vote. Choose To: Group or a living suspect; you can also '
            "select a suspect by clicking their name. A direct question gets that suspect's reply. After three "
            'direct replies from the same suspect, another suspect must speak before you can directly ask them '
            'again. Group remains available. In the message box, Record voice starts the microphone; Stop '
            'recording transcribes into your draft. You can keep typing, edit the result, or Cancel. Nothing is '
            'spoken until you choose Say. Use Inventory while the conversation is waiting for you. Items do not '
            'send or erase the words you are composing. A banana leaves a friendly impression. Muffle prevents a '
            'suspect from speaking clearly until the next night, but they can still vote. Spare Paper adds two '
            'extra copies of your next ballot; multiple copies stack. An armed Guardian Angel prevents your next '
            'death once, including an execution, and stays with the protected body through a swap.'
            '\n\n'
            'Evidence\nThe Detective presents discoveries before discussion. A camera photograph is a genuine'
            ' still image taken after arrivals and before your night action, rather than proof of where someone stayed throughout '
            'the night.'
            '\n\n'
            'Voting\nAfter enough discussion, Call Vote becomes available. Choose another living suspect or '
            'abstain. The highest weighted vote wins; a tie or an abstention winning means no execution. Ballot '
            'results are public. Surviving a vote can unlock more options for your next run.'
            '\n\n'
            'Use Transcript to revisit the conversation. Close this guide to return to the game.'
        )
        self.text_area.setStyleSheet(f'background-color: #1E1E1E; color: #E0E0E0; border: 1px solid #777; font-family: "{readable_body_font().family()}"; font-size: 18px; padding: 16px;')
        layout.addWidget(self.text_area)
        cursor = self.text_area.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.text_area.setTextCursor(cursor)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = GameWindow()
    window.show()
    sys.exit(app.exec_())
