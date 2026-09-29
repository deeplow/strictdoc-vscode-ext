#include <stdio.h>

/**
 * @relation(REQ-1, scope=function)
 */
int lib_x(void) {
    return 1;
}

int lib_y(void) {
    int y = 2;
    return y;
}

// @relation(REQ-2, scope=range_start)
int lib_z(void) {
    return 3;
}
// @relation(REQ-2, scope=range_end)
