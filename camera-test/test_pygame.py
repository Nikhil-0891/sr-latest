#!/usr/bin/env python3
import pygame
import sys

print("Starting pygame test...")
pygame.init()
print("Pygame initialized")

try:
    screen = pygame.display.set_mode((800, 600))
    print("Window created successfully")
    pygame.display.set_caption("Test Window")
    
    clock = pygame.time.Clock()
    running = True
    frame_count = 0
    
    while running and frame_count < 60:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
        
        screen.fill((20, 20, 30))
        pygame.draw.circle(screen, (255, 100, 100), (400, 300), 30)
        pygame.display.flip()
        clock.tick(60)
        frame_count += 1
        
        if frame_count % 10 == 0:
            print(f"Frame {frame_count}")
    
    print("Test complete")
    pygame.quit()
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
