import os
import pygame


SCREEN_WIDTH = 600
SCREEN_HEIGHT = 400


ROLE_INITIAL_POSITIONS = {
    'kyo': (60, 240),
    'chris': (60, 240),
}

ROLE_INITIAL_LIFE = {
    'kyo': 100,
    'chris': 100,
}

ROLE_ACTION_RELATIVE_POSITIONS = {
    'kyo': {
        'stand': ([0, 0], [0, 0]),
        'forward': ([0, -12], [0, -12]),
        'backward': ([0, -12], [0, -12]),
        'crouch': ([0, -1], [0, -1]),
        'jump': ([-5, -75], [5, -75]),
        'punch_light': ([0, 1], [0, 1]),
        'punch_heavy': ([0, 0], [0, 0]),
        'kick_light': ([0, -7], [0, -7]),
        'kick_heavy': ([0, -25], [0, -25]),
        'hurt': ([-10, -3], [10, -3]),
    },
    'chris': {
        'stand': ([0, 0], [0, 0]),
        'forward': ([0, 2], [0, 2]),
        'backward': ([0, 2], [0, 2]),
        'crouch': ([0, 0], [0, 0]),
        'jump': ([-20, -115], [20, -115]),
        'punch_light': ([0, -2], [0, -2]),
        'punch_heavy': ([0, -19], [0, -19]),
        'kick_light': ([0, -2], [0, -2]),
        'kick_heavy': ([0, -3], [0, -3]),
        'hurt': ([-20, -42], [20, -42]),
    }
}

ROLE_ATTACKING_STATE = {
    'kyo': [
        ('punch_light', 2),
        ('punch_heavy', 3),
        ('kick_light', 2),
        ('kick_heavy', 5),
    ],
    'chris': [
        ('punch_light', 2),
        ('punch_heavy', 2),
        ('kick_light', 2),
        ('kick_heavy', 3),
    ]
}

ROLE_ATTACK_ABILITY = {
    'kyo': {
        'punch_light': 5,
        'punch_heavy': 10,
        'kick_light': 5,
        'kick_heavy': 10,
    },
    'chris': {
        'punch_light': 5,
        'punch_heavy': 10,
        'kick_light': 5,
        'kick_heavy': 10,
    }
}

ROLE_DEFENCE_ABILITY = {
    'kyo': 2,
    'chris': 2,
}



class Config:
    def __init__(self, role_name, is_player1, assets_dir='assets'):
        # screen settings
        self.screen_width = SCREEN_WIDTH
        self.screen_height = SCREEN_HEIGHT

        # image location
        self.image_path = os.path.join(assets_dir, 'roles', role_name)

        # role settings
        self.initial_pos = ROLE_INITIAL_POSITIONS[role_name]
        self.initial_life = ROLE_INITIAL_LIFE[role_name]
        self.action_relative_pos = ROLE_ACTION_RELATIVE_POSITIONS[role_name]
        self.speed = 7
        self.attack_ability = ROLE_ATTACK_ABILITY[role_name]
        self.defence_ability = ROLE_DEFENCE_ABILITY[role_name]
        self.attacking_state = ROLE_ATTACKING_STATE[role_name]

        # key settings
        self.key_mapping = {
            pygame.K_a: 'left',
            pygame.K_s: 'down',
            pygame.K_d: 'right',
            pygame.K_w: 'up',
            pygame.K_u: 'u',
            pygame.K_j: 'j',
            pygame.K_i: 'i',
            pygame.K_k: 'k',
        } if is_player1 else {
            pygame.K_LEFT: 'left',
            pygame.K_RIGHT: 'right',
            pygame.K_UP: 'up',
            pygame.K_DOWN: 'down',
            pygame.K_1: 'j',
            pygame.K_2: 'k',
            pygame.K_4: 'u',
            pygame.K_5: 'i',
        }
        self.key2action = {
            'up': 'jump',
            'down': 'crouch',
            'left': 'backward',
            'right': 'forward',
            'j': 'punch_heavy',
            'k': 'kick_heavy',
            'u': 'punch_light',
            'i': 'kick_light',
        }
        self.key2action_reverse = {
            'up': 'jump',
            'down': 'crouch',
            'left': 'forward',
            'right': 'backward',
            'j': 'punch_heavy',
            'k': 'kick_heavy',
            'u': 'punch_light',
            'i': 'kick_light',
        }
