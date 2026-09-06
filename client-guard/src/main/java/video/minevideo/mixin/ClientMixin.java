package video.minevideo.mixin;

import net.minecraft.client.MinecraftClient;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import video.minevideo.CaptureGuard;

@Mixin(MinecraftClient.class)
public abstract class ClientMixin {
    @Inject(method = "tick", at = @At("TAIL"))
    private void minevideoTick(CallbackInfo info) { CaptureGuard.tick(); }
}
