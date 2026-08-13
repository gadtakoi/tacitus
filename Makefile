ifeq ($(OS),Windows_NT)
    RM = del /Q
    MKDIR = mkdir
else
    RM = rm -rf
    MKDIR = mkdir -p
endif

clean:
	$(RM) build1/
