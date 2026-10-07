with open('utils.py', 'r') as f: lines = f.readlines()
start = -1
for i, l in enumerate(lines):
    if 'class SimpleRedisManager:' in l:
        start = i
        break
if start != -1:
    with open('new_redis_manager.txt', 'r') as f: new_class = f.read()
    with open('utils.py', 'w') as f:
        f.writelines(lines[:start])
        f.write(new_class)
    print('Successfully updated utils.py')
else:
    print('Could not find SimpleRedisManager class')
