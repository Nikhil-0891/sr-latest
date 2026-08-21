#include <stdio.h>
#include "pv_rhino.h"

int main() {

    FILE *f1 = fopen("rhino/lib/common/rhino_params.pv", "rb");
    printf("model file: %s\n", f1 ? "OK" : "FAIL");

    FILE *f2 = fopen("Camera_en_mac_v4_0_0.rhn", "rb");
    printf("context file: %s\n", f2 ? "OK" : "FAIL");

    if (f1) fclose(f1);
    if (f2) fclose(f2);

    return 0;
}
