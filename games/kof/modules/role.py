import os
import pygame


class Role:
    def __init__(self, name, reverse, cfg):
        self.name = name
        self.reverse = reverse
        self.cfg = cfg
        self.initial_life, self.rest_life = cfg.initial_life, cfg.initial_life
        # actions
        self.action2imgs, self.action2imgs_reverse, self.action_len, self.action_img_size = self.load_images(cfg.image_path)
        self.cur_action2imgs = self.action2imgs if not self.reverse else self.action2imgs_reverse
        self.action = 'stand'
        self.action_idx = 0
        # positions
        self.bm_pos = [ele for ele in cfg.initial_pos] if not reverse else [cfg.screen_width - cfg.initial_pos[0], cfg.initial_pos[1]]
        self.bm_pos_other = [self.bm_pos[0] + self.action_img_size[self.action][0], self.bm_pos[1]] if not reverse \
                            else [self.bm_pos[0] - self.action_img_size[self.action][0], self.bm_pos[1]]
        self.draw_pos = self.bm_pos if not reverse else self.bm_pos_other
        self.judge_line_x = (self.bm_pos[0] + self.bm_pos_other[0]) / 2
        self.direction = {(False, 'forward'): 1, (False, 'backward'): -1, (True, 'forward'): -1, (True, 'backward'): 1}
        # keys
        self.key2action = cfg.key2action if not self.reverse else cfg.key2action_reverse
        self.keys_to_handle = []
        

    def load_images(self, root):
        action2images, action2images_reverse, action_len, action_img_size = {}, {}, {}, {}
        for dir in sorted(os.listdir(root)):
            images, images_reverse = [], []
            subroot = os.path.join(root, dir)
            if not os.path.isdir(subroot):
                continue
            for file in sorted(os.listdir(subroot)):
                img = pygame.image.load(os.path.join(subroot, file))
                action_img_size[dir] = img.get_size()
                images.append(img)
                images_reverse.append(pygame.transform.flip(img, True, False))
            action2images[dir] = images
            action2images_reverse[dir] = images_reverse
            action_len[dir] = len(images)
        return action2images, action2images_reverse, action_len, action_img_size
    
    
    def process_key(self, event):
        if event.key in self.cfg.key_mapping:
            if event.type == pygame.KEYDOWN:
                key = self.cfg.key_mapping[event.key]
                if key not in self.keys_to_handle:
                    self.keys_to_handle.append(key)
            elif event.type == pygame.KEYUP:
                key = self.cfg.key_mapping[event.key]
                if key in self.keys_to_handle:
                    self.keys_to_handle.remove(key)


    def is_state_stoppable(self):
        if self.action in ['stand', 'forward', 'backward']:
            return True
        if self.action == 'crouch' and self.action_idx == 2:
            return True
        if self.action_idx >= self.action_len[self.action] - 1:
            return True
        return False


    def is_attacking(self):
        return (self.action, self.action_idx) in self.cfg.attacking_state
    

    def is_defencing(self):
        return self.action == 'backward'
    

    def check_and_reverse(self, other_role):
        self.judge_line_x = (self.bm_pos[0] + self.bm_pos_other[0]) / 2
        if not self.reverse and \
           ((self.judge_line_x >= other_role.judge_line_x and self.judge_line_x < other_role.judge_line_x + 2 * self.cfg.speed) and (self.action == 'forward' or other_role.action == 'forward') or \
           (self.judge_line_x >= other_role.judge_line_x and self.judge_line_x < other_role.judge_line_x + 2 * self.cfg.speed) and (self.action == 'forward' and other_role.action == 'forward')):
            self.reverse = not self.reverse
            self.cur_action2imgs = self.action2imgs_reverse
            self.key2action = self.cfg.key2action_reverse
            self.bm_pos, self.bm_pos_other = self.bm_pos_other, self.bm_pos
        elif self.reverse and \
             ((self.judge_line_x <= other_role.judge_line_x and self.judge_line_x > other_role.judge_line_x - 2 * self.cfg.speed) and (self.action == 'forward' or other_role.action == 'forward') or \
             (self.judge_line_x <= other_role.judge_line_x and self.judge_line_x > other_role.judge_line_x - 2 * self.cfg.speed) and (self.action == 'forward' and other_role.action == 'forward')):
            self.reverse = not self.reverse
            self.cur_action2imgs = self.action2imgs
            self.key2action = self.cfg.key2action
            self.bm_pos, self.bm_pos_other = self.bm_pos_other, self.bm_pos


    def update_state(self, next_action):
        if next_action != self.action and self.is_state_stoppable():
            self.action = next_action
            self.action_idx = 0
        elif self.action == 'crouch' and next_action == 'crouch' and self.action_idx == 2:
            return
        else:
            self.action_idx += 1
    
    
    def update_position(self, other_role):
        if self.action in ['forward', 'backward']:
            self.direct = self.direction[(self.reverse, self.action)]
            if not (not self.reverse and (self.bm_pos[0] <= 0 and self.direct < 0 or self.bm_pos_other[0] >= self.cfg.screen_width and self.direct > 0) or self.reverse and (self.bm_pos[0] >= self.cfg.screen_width and self.direct > 0 or self.bm_pos_other[0] <= 0 and self.direct < 0)):
                self.bm_pos[0] += self.direction[(self.reverse, self.action)] * self.cfg.speed
                self.bm_pos_other[0] += self.direction[(self.reverse, self.action)] * self.cfg.speed
        self.check_and_reverse(other_role)
        self.bm_pos_other[0] = self.bm_pos[0] + self.action_img_size[self.action][0] * (1 if not self.reverse else -1)
        self.draw_pos = [p + rela_p for p, rela_p in zip(self.bm_pos, self.cfg.action_relative_pos[self.action][0 if not self.reverse else 1])]
        if self.reverse:
            self.draw_pos[0] -= self.action_img_size[self.action][0]


    def touch_role(self, other_role):
        ckpts = [self.bm_pos_other, other_role.bm_pos_other]
        if self.reverse:
            ckpts.reverse()
        return ckpts[0][0] >= ckpts[1][0]


    def update(self, other_role):
        if other_role.is_attacking() and not self.is_defencing() and self.touch_role(other_role):
            next_action = 'hurt'
            self.rest_life -= other_role.cfg.attack_ability[other_role.action] - self.cfg.defence_ability
        elif not self.keys_to_handle:
            next_action = 'stand'
        else:
            next_action = self.key2action[self.keys_to_handle[-1]]
        self.update_state(next_action)
        self.update_position(other_role)
        

    def show(self, screen):
        action_imgs = self.cur_action2imgs[self.action]
        screen.blit(action_imgs[self.action_idx % len(action_imgs)], self.draw_pos)
