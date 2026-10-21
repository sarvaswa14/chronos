# Chronos Phase 1 build
CC      ?= gcc
CSTD    := -std=gnu11
WARN    := -Wall -Wextra -Wshadow -Wformat=2 -Wformat-security -Wnull-dereference -Wstrict-prototypes -Werror
OPT     ?= -O2 -g
HARDEN  := -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fPIC
BUILD   := build
SRC     := src/rt_core.c src/rt_sync.c src/rt_race.c src/rt_lockorder.c src/rt_out.c src/rt_schedfile.c
OBJ     := $(patsubst src/%.c,$(BUILD)/%.o,$(SRC))
LIB     := $(BUILD)/libchronos.so
LDFLAGS_SO := -shared -pthread -ldl -Wl,-z,relro,-z,now

$(shell mkdir -p $(BUILD)/bugs $(BUILD)/controls $(BUILD)/unit)

PROG_BUGS     := $(patsubst tests/programs/bugs/%.c,$(BUILD)/bugs/%,$(wildcard tests/programs/bugs/*.c)) \
                 $(patsubst tests/programs/bugs/%.cpp,$(BUILD)/bugs/%,$(wildcard tests/programs/bugs/*.cpp))
PROG_CONTROLS := $(patsubst tests/programs/controls/%.c,$(BUILD)/controls/%,$(wildcard tests/programs/controls/*.c)) \
                 $(patsubst tests/programs/controls/%.cpp,$(BUILD)/controls/%,$(wildcard tests/programs/controls/*.cpp))
PROG_UNIT     := $(patsubst tests/unit/%.c,$(BUILD)/unit/%,$(wildcard tests/unit/*.c))

.PHONY: all lib programs clean test asan
all: lib programs

lib: $(LIB)

$(BUILD)/%.o: src/%.c src/rt.h
	$(CC) $(CSTD) $(WARN) $(OPT) $(HARDEN) -c $< -o $@

$(LIB): $(OBJ)
	$(CC) $(OBJ) $(LDFLAGS_SO) -o $@

programs: $(PROG_BUGS) $(PROG_CONTROLS) $(PROG_UNIT) $(BUILD)/schedule_check

$(BUILD)/bugs/%: tests/programs/bugs/%.c include/chronos.h
	$(CC) $(CSTD) -Wall -Wextra -O1 -g -Iinclude $< -o $@ -pthread

$(BUILD)/controls/%: tests/programs/controls/%.c include/chronos.h
	$(CC) $(CSTD) -Wall -Wextra -O1 -g -Iinclude $< -o $@ -pthread

$(BUILD)/unit/%: tests/unit/%.c include/chronos.h
	$(CC) $(CSTD) -Wall -Wextra -O1 -g -Iinclude $< -o $@ -pthread

$(BUILD)/bugs/%: tests/programs/bugs/%.cpp include/chronos.h
	$(CXX) -std=gnu++17 -Wall -Wextra -O1 -g -Iinclude $< -o $@ -pthread

$(BUILD)/controls/%: tests/programs/controls/%.cpp include/chronos.h
	$(CXX) -std=gnu++17 -Wall -Wextra -O1 -g -Iinclude $< -o $@ -pthread

# fuzz/regression harness for the schedule + override parsers (ASan+UBSan)
$(BUILD)/schedule_check: tests/fuzz/schedule_check.c src/rt_schedfile.c src/rt.h
	$(CC) $(CSTD) $(WARN) -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Isrc \
	  tests/fuzz/schedule_check.c src/rt_schedfile.c -o $@

clean:
	rm -rf $(BUILD)
	mkdir -p $(BUILD)/bugs $(BUILD)/controls $(BUILD)/unit

.PHONY: phase2-test phase2-eval
phase2-test: all
	python3 tests/test_phase2.py

phase2-eval: all
	python3 tools/phase2_eval.py

test: all
	python3 tests/test_runner.py

asan: all
	python3 tests/test_runner.py -k TestSanitizers
