import os
import sys
import pygame


def quit_game():
    pygame.quit()
    sys.exit()


def draw_lifebar(screen, role1, role2, location):
    pos, width, height = location
    role1_life_ratio = max(role1.rest_life / role1.initial_life, 0)
    role2_life_ratio = max(role2.rest_life / role2.initial_life, 0)
    role1_life_left = pygame.Rect(pos[0], pos[1], int(width * role1_life_ratio), height)
    role1_life_consumed = pygame.Rect(pos[0] + int(width * role1_life_ratio), pos[1], width - int(width * role1_life_ratio), height)
    role2_life_left = pygame.Rect(screen.get_size()[0] - pos[0] - int(width * role2_life_ratio), pos[1], int(width * role2_life_ratio), height)
    role2_life_consumed = pygame.Rect(screen.get_size()[0] - pos[0] - width, pos[1], width - int(width * role2_life_ratio), height)

    pygame.draw.rect(screen, 'yellow', role1_life_left)
    pygame.draw.rect(screen, 'red', role1_life_consumed)
    pygame.draw.rect(screen, 'yellow', role2_life_left)
    pygame.draw.rect(screen, 'red', role2_life_consumed)


def load_background(root):
    bg = []
    for file in sorted(os.listdir(root)):
        path = os.path.join(root, file)
        if os.path.isfile(path):
            bg.append(pygame.image.load(path))
    return bg


def draw_background(screen, bg_imgs, frame):
    screen.blit(
        pygame.transform.scale(
            bg_imgs[(frame // 2 + frame % 2) % len(bg_imgs)],
            screen.get_size(),
        ),
        [0, 0],
    )


def show_result_screen(screen, winner):
    f = pygame.font.SysFont([], size=60, bold=True, italic=False)
    text = f.render(f'Player {winner} Win!', True, (255, 0,0), (255, 255, 255))
    rect = text.get_rect()
    rect.center = (screen.get_size()[0] // 2, screen.get_size()[1] // 2)
    screen.blit(text, rect)
    pygame.display.update()
