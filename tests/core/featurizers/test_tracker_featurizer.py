from typing import Text, Dict, List, Optional

import numpy as np
from scipy import sparse
import pytest

from rasa.core.featurizers.single_state_featurizer import (
    SingleStateFeaturizer,
    IntentTokenizerSingleStateFeaturizer,
)
from rasa.core.featurizers.tracker_featurizers import (
    TrackerFeaturizer,
    FullDialogueTrackerFeaturizer,
    MaxHistoryTrackerFeaturizer,
    IntentMaxHistoryTrackerFeaturizer,
)
from rasa.shared.core.domain import Domain
from rasa.shared.nlu.interpreter import RegexInterpreter
from tests.core.utilities import tracker_from_dialogue_file, user_uttered
from rasa.shared.nlu.training_data.features import Features
from rasa.shared.nlu.constants import INTENT, ACTION_NAME, FEATURE_TYPE_SENTENCE
from rasa.shared.core.constants import (
    ACTION_LISTEN_NAME,
    ACTION_UNLIKELY_INTENT_NAME,
    USER,
    PREVIOUS_ACTION,
)
from rasa.shared.core.events import ActionExecuted, UserUttered
from rasa.shared.core.trackers import DialogueStateTracker
from rasa.utils.tensorflow.constants import LABEL_PAD_ID


def test_fail_to_load_non_existent_featurizer():
    assert TrackerFeaturizer.load("non_existent_class") is None


def test_persist_and_load_tracker_featurizer(tmp_path: Text, moodbot_domain: Domain):
    state_featurizer = SingleStateFeaturizer()
    state_featurizer.prepare_for_training(moodbot_domain, RegexInterpreter())
    tracker_featurizer = MaxHistoryTrackerFeaturizer(state_featurizer)

    tracker_featurizer.persist(tmp_path)

    loaded_tracker_featurizer = TrackerFeaturizer.load(tmp_path)

    assert loaded_tracker_featurizer is not None
    assert loaded_tracker_featurizer.state_featurizer is not None


def test_convert_action_labels_to_ids(domain: Domain):
    trackers_as_actions = [
        ["utter_greet", "utter_channel"],
        ["utter_greet", "utter_default", "utter_goodbye"],
    ]

    tracker_featurizer = TrackerFeaturizer()

    actual_output = tracker_featurizer._convert_labels_to_ids(
        trackers_as_actions, domain
    )
    expected_output = np.array([np.array([15, 12]), np.array([15, 13, 14])])

    assert expected_output.size == actual_output.size
    for expected_array, actual_array in zip(expected_output, actual_output):
        assert np.all(expected_array == actual_array)


def test_convert_intent_labels_to_ids(domain: Domain):
    trackers_as_intents = [
        ["next_intent", "nlu_fallback", "out_of_scope", "restart"],
        ["greet", "hello", "affirm"],
    ]

    tracker_featurizer = IntentMaxHistoryTrackerFeaturizer()

    actual_labels = tracker_featurizer._convert_labels_to_ids(
        trackers_as_intents, domain
    )

    expected_labels = np.array([[7, 8, 9, 10], [5, 6, 0, LABEL_PAD_ID]])
    assert expected_labels.size == actual_labels.size
    assert expected_labels.shape == actual_labels.shape
    assert np.all(expected_labels == actual_labels)


def test_featurize_trackers_raises_on_missing_state_featurizer(domain: Domain):
    tracker_featurizer = TrackerFeaturizer()

    with pytest.raises(ValueError):
        tracker_featurizer.featurize_trackers([], domain, RegexInterpreter())


@pytest.fixture
def moodbot_features(
    request, moodbot_domain: Domain
) -> Dict[Text, Dict[Text, Features]]:
    """Makes intent and action features for the moodbot domain to faciliate
    making expected state features.

    Returns:
      A dict containing dicts for mapping action and intent names to features.
    """
    origin = getattr(request, "param", "SingleStateFeaturizer")
    action_shape = (1, len(moodbot_domain.action_names_or_texts))
    actions = {}
    for index, action in enumerate(moodbot_domain.action_names_or_texts):
        actions[action] = Features(
            sparse.coo_matrix(([1.0], [[0], [index]]), shape=action_shape),
            FEATURE_TYPE_SENTENCE,
            ACTION_NAME,
            origin,
        )
    intent_shape = (1, len(moodbot_domain.intents))
    intents = {}
    for index, intent in enumerate(moodbot_domain.intents):
        intents[intent] = Features(
            sparse.coo_matrix(([1.0], [[0], [index]]), shape=intent_shape),
            FEATURE_TYPE_SENTENCE,
            INTENT,
            origin,
        )
    return {"intents": intents, "actions": actions}


def compare_featurized_states(
    states1: List[Dict[Text, List[Features]]], states2: List[Dict[Text, List[Features]]]
) -> bool:
    """Compares two lists of featurized states and returns True if they
    are identical and False otherwise.
    """

    if len(states1) != len(states2):
        return False

    for state1, state2 in zip(states1, states2):
        if state1.keys() != state2.keys():
            return False
        for key in state1.keys():
            for feature1, feature2 in zip(state1[key], state2[key]):
                if np.any((feature1.features != feature2.features).toarray()):
                    return False
                if feature1.origin != feature2.origin:
                    return False
                if feature1.attribute != feature2.attribute:
                    return False
                if feature1.type != feature2.type:
                    return False
    return True


@pytest.fixture
def moodbot_tracker(moodbot_domain: Domain) -> DialogueStateTracker:
    return tracker_from_dialogue_file(
        "data/test_dialogues/moodbot.json", moodbot_domain
    )


@pytest.mark.parametrize("insert_action_unlikely_intent", [False, True])
def test_featurize_trackers_with_full_dialogue_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    moodbot_features: Dict[Text, Dict[Text, Features]],
    insert_action_unlikely_intent: bool,
):

    # Insert some `action_unlikely_intent` actions to test that they are ignored.
    if insert_action_unlikely_intent:
        moodbot_tracker = moodbot_tracker.copy()
        i = 0
        for event in list(moodbot_tracker.events):
            if isinstance(event, UserUttered):
                i += 1
                moodbot_tracker.events.insert(
                    i, ActionExecuted(ACTION_UNLIKELY_INTENT_NAME)
                )
            i += 1

    state_featurizer = SingleStateFeaturizer()
    tracker_featurizer = FullDialogueTrackerFeaturizer(state_featurizer)

    actual_features, actual_labels, entity_tags = tracker_featurizer.featurize_trackers(
        [moodbot_tracker], moodbot_domain, RegexInterpreter()
    )

    expected_features = [
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
            {ACTION_NAME: [moodbot_features["actions"]["utter_did_that_help"]]},
            {
                INTENT: [moodbot_features["intents"]["deny"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
        ]
    ]

    assert actual_features is not None
    assert len(actual_features) == len(expected_features)

    for actual, expected in zip(actual_features, expected_features):
        assert compare_featurized_states(actual, expected)

    expected_labels = np.array([[0, 15, 0, 12, 13, 0, 14]])
    assert actual_labels is not None
    assert len(actual_labels) == 1
    for actual, expected in zip(actual_labels, expected_labels):
        assert np.all(actual == expected)

    # moodbot doesn't contain e2e entities
    assert not any([any(turn_tags) for turn_tags in entity_tags])


def test_create_state_features_with_full_dialogue_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    moodbot_features: Dict[Text, Dict[Text, Features]],
):
    state_featurizer = SingleStateFeaturizer()
    tracker_featurizer = FullDialogueTrackerFeaturizer(state_featurizer)
    interpreter = RegexInterpreter()
    state_featurizer.prepare_for_training(moodbot_domain, interpreter)
    actual_features = tracker_featurizer.create_state_features(
        [moodbot_tracker], moodbot_domain, interpreter
    )

    expected_features = [
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
            {ACTION_NAME: [moodbot_features["actions"]["utter_did_that_help"]]},
            {
                INTENT: [moodbot_features["intents"]["deny"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_goodbye"]]},
        ]
    ]

    assert actual_features is not None
    assert len(actual_features) == len(expected_features)

    for actual, expected in zip(actual_features, expected_features):
        assert compare_featurized_states(actual, expected)


def test_prediction_states_with_full_dialogue_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker, moodbot_domain: Domain
):
    state_featurizer = SingleStateFeaturizer()
    tracker_featurizer = FullDialogueTrackerFeaturizer(state_featurizer)
    actual_states = tracker_featurizer.prediction_states(
        [moodbot_tracker], moodbot_domain,
    )

    expected_states = [
        [
            {},
            {
                USER: {INTENT: "greet"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {USER: {INTENT: "greet"}, PREVIOUS_ACTION: {ACTION_NAME: "utter_greet"},},
            {
                USER: {INTENT: "mood_unhappy"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {
                USER: {INTENT: "mood_unhappy"},
                PREVIOUS_ACTION: {ACTION_NAME: "utter_cheer_up"},
            },
            {
                USER: {INTENT: "mood_unhappy"},
                PREVIOUS_ACTION: {ACTION_NAME: "utter_did_that_help"},
            },
            {
                USER: {INTENT: "deny"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {USER: {INTENT: "deny"}, PREVIOUS_ACTION: {ACTION_NAME: "utter_goodbye"},},
        ]
    ]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected


def test_prediction_states_hide_rule_states_with_full_dialogue_tracker_featurizer(
    moodbot_domain: Domain,
):

    state_featurizer = SingleStateFeaturizer()
    tracker_featurizer = FullDialogueTrackerFeaturizer(state_featurizer)

    rule_tracker = DialogueStateTracker.from_events(
        "default",
        [
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("greet"),
            ActionExecuted("utter_greet", hide_rule_turn=True),
            ActionExecuted(ACTION_LISTEN_NAME, hide_rule_turn=True),
        ],
        domain=moodbot_domain,
    )

    actual_states = tracker_featurizer.prediction_states(
        [rule_tracker], moodbot_domain, ignore_rule_only_turns=True,
    )

    expected_states = [
        [
            {},
            {
                USER: {INTENT: "greet"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
        ],
    ]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected

    embedded_rule_tracker = DialogueStateTracker.from_events(
        "default",
        [
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("greet"),
            ActionExecuted("utter_greet", hide_rule_turn=True),
            ActionExecuted(ACTION_LISTEN_NAME, hide_rule_turn=True),
            user_uttered("mood_great"),
            ActionExecuted("utter_happy"),
            ActionExecuted(ACTION_LISTEN_NAME),
        ],
        domain=moodbot_domain,
    )

    actual_states = tracker_featurizer.prediction_states(
        [embedded_rule_tracker], moodbot_domain, ignore_rule_only_turns=True,
    )

    expected_states = [
        [
            {},
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: "utter_happy"},
            },
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
        ]
    ]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected


def test_prediction_states_ignores_action_intent_unlikely_with_full_dialogue_tracker_featurizer(
    moodbot_domain: Domain,
):

    state_featurizer = SingleStateFeaturizer()
    tracker_featurizer = FullDialogueTrackerFeaturizer(state_featurizer)

    tracker = DialogueStateTracker.from_events(
        "default",
        [
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("greet"),
            ActionExecuted(ACTION_UNLIKELY_INTENT_NAME),
            ActionExecuted("utter_greet"),
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("mood_great"),
            ActionExecuted(ACTION_UNLIKELY_INTENT_NAME),
            ActionExecuted("utter_happy"),
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("goodbye"),
        ],
        domain=moodbot_domain,
    )

    actual_states = tracker_featurizer.prediction_states([tracker], moodbot_domain,)

    expected_states = [
        [
            {},
            {
                USER: {INTENT: "greet"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {USER: {INTENT: "greet"}, PREVIOUS_ACTION: {ACTION_NAME: "utter_greet"},},
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: "utter_happy"},
            },
            {
                USER: {INTENT: "goodbye"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
        ]
    ]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected


@pytest.mark.parametrize(
    "max_history,insert_action_unlikely_intent",
    [[None, False], [2, False], [None, True], [2, True],],
)
def test_featurize_trackers_with_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    moodbot_features: Dict[Text, Dict[Text, Features]],
    insert_action_unlikely_intent: bool,
    max_history: Optional[int],
):

    # Insert some `action_unlikely_intent` actions to test that they are ignored.
    if insert_action_unlikely_intent:
        moodbot_tracker = moodbot_tracker.copy()
        i = 0
        for event in list(moodbot_tracker.events):
            if isinstance(event, UserUttered):
                i += 1
                moodbot_tracker.events.insert(
                    i, ActionExecuted(ACTION_UNLIKELY_INTENT_NAME)
                )
            i += 1

    state_featurizer = SingleStateFeaturizer()
    tracker_featurizer = MaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history
    )

    actual_features, actual_labels, entity_tags = tracker_featurizer.featurize_trackers(
        [moodbot_tracker], moodbot_domain, RegexInterpreter()
    )

    expected_features = [
        [{},],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
            {ACTION_NAME: [moodbot_features["actions"]["utter_did_that_help"]]},
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
            {ACTION_NAME: [moodbot_features["actions"]["utter_did_that_help"]]},
            {
                INTENT: [moodbot_features["intents"]["deny"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
        ],
    ]
    if max_history is not None:
        expected_features = [x[-max_history:] for x in expected_features]

    assert actual_features is not None
    assert len(actual_features) == len(expected_features)

    for actual, expected in zip(actual_features, expected_features):
        assert compare_featurized_states(actual, expected)

    expected_labels = np.array([[0, 15, 0, 12, 13, 0, 14]]).T

    assert actual_labels is not None
    assert actual_labels.shape == expected_labels.shape
    assert np.all(actual_labels == expected_labels)

    # moodbot doesn't contain e2e entities
    assert not any([any(turn_tags) for turn_tags in entity_tags])


@pytest.mark.parametrize(
    "remove_duplicates,max_history",
    [[True, None], [True, 2], [False, None], [False, 2],],
)
def test_deduplicate_featurize_trackers_with_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    moodbot_features: Dict[Text, Dict[Text, Features]],
    remove_duplicates: bool,
    max_history: Optional[int],
):
    state_featurizer = SingleStateFeaturizer()
    tracker_featurizer = MaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history, remove_duplicates=remove_duplicates
    )

    # Add Duplicate moodbot_tracker states should get removed.
    actual_features, actual_labels, entity_tags = tracker_featurizer.featurize_trackers(
        [moodbot_tracker, moodbot_tracker], moodbot_domain, RegexInterpreter()
    )

    expected_features = [
        [{},],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
            {ACTION_NAME: [moodbot_features["actions"]["utter_did_that_help"]]},
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
            {ACTION_NAME: [moodbot_features["actions"]["utter_did_that_help"]]},
            {
                INTENT: [moodbot_features["intents"]["deny"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
        ],
    ]
    if max_history is not None:
        expected_features = [x[-max_history:] for x in expected_features]
    if not remove_duplicates:
        expected_features = expected_features * 2

    assert actual_features is not None
    assert len(actual_features) == len(expected_features)

    for actual, expected in zip(actual_features, expected_features):
        assert compare_featurized_states(actual, expected)

    expected_labels = np.array([[0, 15, 0, 12, 13, 0, 14]]).T
    if not remove_duplicates:
        expected_labels = np.vstack([expected_labels] * 2)

    assert actual_labels is not None
    assert actual_labels.shape == expected_labels.shape
    assert np.all(actual_labels == expected_labels)

    # moodbot doesn't contain e2e entities
    assert not any([any(turn_tags) for turn_tags in entity_tags])


@pytest.mark.parametrize("max_history", [None, 2])
def test_create_state_features_with_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    moodbot_features: Dict[Text, Dict[Text, Features]],
    max_history: Optional[int],
):
    state_featurizer = SingleStateFeaturizer()
    tracker_featurizer = MaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history
    )
    interpreter = RegexInterpreter()
    state_featurizer.prepare_for_training(moodbot_domain, interpreter)
    actual_features = tracker_featurizer.create_state_features(
        [moodbot_tracker], moodbot_domain, interpreter
    )

    expected_features = [
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
            {ACTION_NAME: [moodbot_features["actions"]["utter_did_that_help"]]},
            {
                INTENT: [moodbot_features["intents"]["deny"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_goodbye"]]},
        ]
    ]
    if max_history is not None:
        expected_features = [x[-max_history:] for x in expected_features]

    assert actual_features is not None
    assert len(actual_features) == len(expected_features)

    for actual, expected in zip(actual_features, expected_features):
        assert compare_featurized_states(actual, expected)


@pytest.mark.parametrize("max_history", [None, 2])
def test_prediction_states_with_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    max_history: Optional[int],
):

    state_featurizer = SingleStateFeaturizer()
    tracker_featurizer = MaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history
    )
    actual_states = tracker_featurizer.prediction_states(
        [moodbot_tracker], moodbot_domain,
    )

    expected_states = [
        [
            {},
            {
                USER: {INTENT: "greet"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {USER: {INTENT: "greet"}, PREVIOUS_ACTION: {ACTION_NAME: "utter_greet"},},
            {
                USER: {INTENT: "mood_unhappy"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {
                USER: {INTENT: "mood_unhappy"},
                PREVIOUS_ACTION: {ACTION_NAME: "utter_cheer_up"},
            },
            {
                USER: {INTENT: "mood_unhappy"},
                PREVIOUS_ACTION: {ACTION_NAME: "utter_did_that_help"},
            },
            {
                USER: {INTENT: "deny"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {USER: {INTENT: "deny"}, PREVIOUS_ACTION: {ACTION_NAME: "utter_goodbye"},},
        ]
    ]
    if max_history is not None:
        expected_states = [x[-max_history:] for x in expected_states]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected


@pytest.mark.parametrize("max_history", [None, 2])
def test_prediction_states_hide_rule_states_with_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    max_history: Optional[int],
):

    state_featurizer = SingleStateFeaturizer()
    tracker_featurizer = MaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history
    )

    rule_tracker = DialogueStateTracker.from_events(
        "default",
        [
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("greet"),
            ActionExecuted("utter_greet", hide_rule_turn=True),
            ActionExecuted(ACTION_LISTEN_NAME, hide_rule_turn=True),
        ],
        domain=moodbot_domain,
    )

    actual_states = tracker_featurizer.prediction_states(
        [rule_tracker], moodbot_domain, ignore_rule_only_turns=True,
    )

    expected_states = [
        [
            {},
            {
                USER: {INTENT: "greet"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
        ],
    ]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected

    embedded_rule_tracker = DialogueStateTracker.from_events(
        "default",
        [
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("greet"),
            ActionExecuted("utter_greet", hide_rule_turn=True),
            ActionExecuted(ACTION_LISTEN_NAME, hide_rule_turn=True),
            user_uttered("mood_great"),
            ActionExecuted("utter_happy"),
            ActionExecuted(ACTION_LISTEN_NAME),
        ],
        domain=moodbot_domain,
    )

    actual_states = tracker_featurizer.prediction_states(
        [embedded_rule_tracker], moodbot_domain, ignore_rule_only_turns=True,
    )

    expected_states = [
        [
            {},
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: "utter_happy"},
            },
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
        ]
    ]

    if max_history is not None:
        expected_states = [x[-max_history:] for x in expected_states]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected


@pytest.mark.parametrize("max_history", [None, 3])
def test_prediction_states_ignores_action_intent_unlikely_with_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    max_history: Optional[int],
):

    state_featurizer = SingleStateFeaturizer()
    tracker_featurizer = MaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history
    )

    tracker = DialogueStateTracker.from_events(
        "default",
        [
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("greet"),
            ActionExecuted(ACTION_UNLIKELY_INTENT_NAME),
            ActionExecuted("utter_greet"),
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("mood_great"),
            ActionExecuted(ACTION_UNLIKELY_INTENT_NAME),
            ActionExecuted("utter_happy"),
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("goodbye"),
        ],
        domain=moodbot_domain,
    )

    actual_states = tracker_featurizer.prediction_states([tracker], moodbot_domain,)

    expected_states = [
        [
            {},
            {
                USER: {INTENT: "greet"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {USER: {INTENT: "greet"}, PREVIOUS_ACTION: {ACTION_NAME: "utter_greet"},},
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: "utter_happy"},
            },
            {
                USER: {INTENT: "goodbye"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
        ]
    ]

    if max_history is not None:
        expected_states = [x[-max_history:] for x in expected_states]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected


@pytest.mark.parametrize(
    "max_history,moodbot_features,insert_action_unlikely_intent",
    [
        [None, "IntentTokenizerSingleStateFeaturizer", False],
        [2, "IntentTokenizerSingleStateFeaturizer", False],
        [None, "IntentTokenizerSingleStateFeaturizer", True],
        [2, "IntentTokenizerSingleStateFeaturizer", True],
    ],
    indirect=["moodbot_features"],
)
def test_featurize_trackers_with_intent_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    moodbot_features: Dict[Text, Dict[Text, Features]],
    insert_action_unlikely_intent: bool,
    max_history: Optional[int],
):

    # Insert some `action_unlikely_intent` actions to test that they are ignored.
    if insert_action_unlikely_intent:
        moodbot_tracker = moodbot_tracker.copy()
        i = 0
        for event in list(moodbot_tracker.events):
            if isinstance(event, UserUttered):
                i += 1
                moodbot_tracker.events.insert(
                    i, ActionExecuted(ACTION_UNLIKELY_INTENT_NAME)
                )
            i += 1

    state_featurizer = IntentTokenizerSingleStateFeaturizer()
    tracker_featurizer = IntentMaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history
    )

    actual_features, actual_labels, entity_tags = tracker_featurizer.featurize_trackers(
        [moodbot_tracker], moodbot_domain, RegexInterpreter()
    )

    expected_features = [
        [{},],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
            {ACTION_NAME: [moodbot_features["actions"]["utter_did_that_help"]]},
        ],
    ]
    if max_history is not None:
        expected_features = [x[-max_history:] for x in expected_features]

    assert actual_features is not None
    assert len(actual_features) == len(expected_features)

    for actual, expected in zip(actual_features, expected_features):
        assert compare_featurized_states(actual, expected)

    expected_labels = np.array([[5, 7, 3]]).T

    assert actual_labels is not None
    assert actual_labels.shape == expected_labels.shape
    assert np.all(actual_labels == expected_labels)

    # moodbot doesn't contain e2e entities
    assert not any([any(turn_tags) for turn_tags in entity_tags])


@pytest.mark.parametrize(
    "remove_duplicates,max_history,moodbot_features",
    [
        [True, None, "IntentTokenizerSingleStateFeaturizer"],
        [True, 2, "IntentTokenizerSingleStateFeaturizer"],
        [False, None, "IntentTokenizerSingleStateFeaturizer"],
        [False, 2, "IntentTokenizerSingleStateFeaturizer"],
    ],
    indirect=["moodbot_features"],
)
def test_deduplicate_featurize_trackers_with_intent_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    moodbot_features: Dict[Text, Dict[Text, Features]],
    remove_duplicates: bool,
    max_history: Optional[int],
):
    state_featurizer = IntentTokenizerSingleStateFeaturizer()
    tracker_featurizer = IntentMaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history, remove_duplicates=remove_duplicates
    )

    # Add Duplicate moodbot_tracker states should get removed.
    actual_features, actual_labels, entity_tags = tracker_featurizer.featurize_trackers(
        [moodbot_tracker, moodbot_tracker], moodbot_domain, RegexInterpreter()
    )

    expected_features = [
        [{},],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
        ],
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
            {ACTION_NAME: [moodbot_features["actions"]["utter_did_that_help"]]},
        ],
    ]

    if max_history is not None:
        expected_features = [x[-max_history:] for x in expected_features]
    if not remove_duplicates:
        expected_features = expected_features * 2

    assert actual_features is not None
    assert len(actual_features) == len(expected_features)

    for actual, expected in zip(actual_features, expected_features):
        assert compare_featurized_states(actual, expected)

    expected_labels = np.array([[5, 7, 3]]).T
    if not remove_duplicates:
        expected_labels = np.vstack([expected_labels] * 2)

    assert actual_labels is not None
    assert actual_labels.shape == expected_labels.shape
    assert np.all(actual_labels == expected_labels)

    # moodbot doesn't contain e2e entities
    assert not any([any(turn_tags) for turn_tags in entity_tags])


@pytest.mark.parametrize(
    "max_history,moodbot_features",
    [
        [None, "IntentTokenizerSingleStateFeaturizer"],
        [2, "IntentTokenizerSingleStateFeaturizer"],
    ],
    indirect=["moodbot_features"],
)
def test_create_state_features_with_intent_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    moodbot_features: Dict[Text, Dict[Text, Features]],
    max_history: Optional[int],
):

    # IntentMaxHistoryTrackerFeaturizer prediction is only done after
    # a UserUttered event so remove the last BotUttered and
    # ActionExecuted events.
    moodbot_tracker = moodbot_tracker.copy()
    moodbot_tracker.events.pop()
    moodbot_tracker.events.pop()

    state_featurizer = IntentTokenizerSingleStateFeaturizer()
    tracker_featurizer = IntentMaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history
    )
    interpreter = RegexInterpreter()
    state_featurizer.prepare_for_training(moodbot_domain, interpreter)
    actual_features = tracker_featurizer.create_state_features(
        [moodbot_tracker], moodbot_domain, interpreter
    )

    expected_features = [
        [
            {},
            {
                INTENT: [moodbot_features["intents"]["greet"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_greet"]]},
            {
                INTENT: [moodbot_features["intents"]["mood_unhappy"]],
                ACTION_NAME: [moodbot_features["actions"][ACTION_LISTEN_NAME]],
            },
            {ACTION_NAME: [moodbot_features["actions"]["utter_cheer_up"]]},
            {ACTION_NAME: [moodbot_features["actions"]["utter_did_that_help"]]},
        ],
    ]
    if max_history is not None:
        expected_features = [x[-max_history:] for x in expected_features]

    assert actual_features is not None
    assert len(actual_features) == len(expected_features)

    for actual, expected in zip(actual_features, expected_features):
        assert compare_featurized_states(actual, expected)


@pytest.mark.parametrize("max_history", [None, 2])
def test_prediction_states_with_intent_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    max_history: Optional[int],
):

    # IntentMaxHistoryTrackerFeaturizer prediction is only done after
    # a UserUttered event so remove the last BotUttered and
    # ActionExecuted events.
    moodbot_tracker = moodbot_tracker.copy()
    moodbot_tracker.events.pop()
    moodbot_tracker.events.pop()

    state_featurizer = IntentTokenizerSingleStateFeaturizer()
    tracker_featurizer = IntentMaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history
    )
    actual_states = tracker_featurizer.prediction_states(
        [moodbot_tracker], moodbot_domain,
    )

    expected_states = [
        [
            {},
            {
                USER: {INTENT: "greet"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {USER: {INTENT: "greet"}, PREVIOUS_ACTION: {ACTION_NAME: "utter_greet"},},
            {
                USER: {INTENT: "mood_unhappy"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {
                USER: {INTENT: "mood_unhappy"},
                PREVIOUS_ACTION: {ACTION_NAME: "utter_cheer_up"},
            },
            {
                USER: {INTENT: "mood_unhappy"},
                PREVIOUS_ACTION: {ACTION_NAME: "utter_did_that_help"},
            },
        ]
    ]
    if max_history is not None:
        expected_states = [x[-max_history:] for x in expected_states]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected


@pytest.mark.parametrize("max_history", [None, 2])
def test_prediction_states_hide_rule_states_with_intent_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    max_history: Optional[int],
):

    state_featurizer = IntentTokenizerSingleStateFeaturizer()
    tracker_featurizer = IntentMaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history
    )

    rule_tracker = DialogueStateTracker.from_events(
        "default",
        [
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("greet"),
            ActionExecuted("utter_greet", hide_rule_turn=True),
            ActionExecuted(ACTION_LISTEN_NAME, hide_rule_turn=True),
        ],
        domain=moodbot_domain,
    )

    actual_states = tracker_featurizer.prediction_states(
        [rule_tracker], moodbot_domain, ignore_rule_only_turns=True,
    )

    expected_states = [[{}]]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected

    embedded_rule_tracker = DialogueStateTracker.from_events(
        "default",
        [
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("greet"),
            ActionExecuted("utter_greet", hide_rule_turn=True),
            ActionExecuted(ACTION_LISTEN_NAME, hide_rule_turn=True),
            user_uttered("mood_great"),
        ],
        domain=moodbot_domain,
    )

    actual_states = tracker_featurizer.prediction_states(
        [embedded_rule_tracker], moodbot_domain, ignore_rule_only_turns=True,
    )

    expected_states = [[{},]]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected


@pytest.mark.parametrize("max_history", [None, 3])
def test_prediction_states_ignores_action_intent_unlikely_with_intent_max_history_tracker_featurizer(
    moodbot_tracker: DialogueStateTracker,
    moodbot_domain: Domain,
    max_history: Optional[int],
):

    state_featurizer = IntentTokenizerSingleStateFeaturizer()
    tracker_featurizer = IntentMaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history
    )

    tracker = DialogueStateTracker.from_events(
        "default",
        [
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("greet"),
            ActionExecuted(ACTION_UNLIKELY_INTENT_NAME),
            ActionExecuted("utter_greet"),
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("mood_great"),
            ActionExecuted(ACTION_UNLIKELY_INTENT_NAME),
            ActionExecuted("utter_happy"),
            ActionExecuted(ACTION_LISTEN_NAME),
            user_uttered("goodbye"),
        ],
        domain=moodbot_domain,
    )

    actual_states = tracker_featurizer.prediction_states([tracker], moodbot_domain,)

    expected_states = [
        [
            {},
            {
                USER: {INTENT: "greet"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {USER: {INTENT: "greet"}, PREVIOUS_ACTION: {ACTION_NAME: "utter_greet"},},
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: ACTION_LISTEN_NAME},
            },
            {
                USER: {INTENT: "mood_great"},
                PREVIOUS_ACTION: {ACTION_NAME: "utter_happy"},
            },
        ]
    ]

    if max_history is not None:
        expected_states = [x[-max_history:] for x in expected_states]

    assert actual_states is not None
    assert len(actual_states) == len(expected_states)

    for actual, expected in zip(actual_states, expected_states):
        assert actual == expected


@pytest.mark.parametrize("remove_duplicates", [True, False])
@pytest.mark.parametrize("max_history", [None, 2])
def test_multilabels_with_intent_max_history_tracker_featurizer(
    moodbot_domain: Domain, max_history: Optional[int], remove_duplicates: bool
):
    state_featurizer = IntentTokenizerSingleStateFeaturizer()
    tracker_featurizer = IntentMaxHistoryTrackerFeaturizer(
        state_featurizer, max_history=max_history, remove_duplicates=remove_duplicates,
    )

    event_list1 = [
        ActionExecuted(ACTION_LISTEN_NAME),
        user_uttered("greet"),
        ActionExecuted("utter_greet"),
        ActionExecuted(ACTION_LISTEN_NAME),
        user_uttered("mood_great"),
    ]
    tracker1 = DialogueStateTracker.from_events(
        "default", event_list1, domain=moodbot_domain
    )
    event_list2 = [
        ActionExecuted(ACTION_LISTEN_NAME),
        user_uttered("greet"),
        ActionExecuted("utter_greet"),
        ActionExecuted(ACTION_LISTEN_NAME),
        user_uttered("mood_unhappy"),
    ]
    tracker2 = DialogueStateTracker.from_events(
        "default", event_list2, domain=moodbot_domain
    )

    _1, actual_labels, _2 = tracker_featurizer.featurize_trackers(
        [tracker1, tracker2], moodbot_domain, RegexInterpreter()
    )

    greet_index = 5
    mood_great_index = 6
    mood_unhappy_index = 7

    if remove_duplicates:
        expected_labels = np.array(
            [
                [greet_index, -1],
                [mood_great_index, mood_unhappy_index],
                [mood_unhappy_index, mood_great_index],
            ]
        )
    else:
        expected_labels = np.array(
            [
                [greet_index, -1],
                [mood_great_index, mood_unhappy_index],
                [greet_index, -1],
                [mood_unhappy_index, mood_great_index],
            ]
        )

    assert actual_labels is not None
    assert actual_labels.shape == expected_labels.shape
    assert np.all(actual_labels == expected_labels)
